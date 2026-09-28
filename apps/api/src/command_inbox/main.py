"""FastAPI application: middleware, error model, health, metrics, live updates, routers."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from command_inbox.auth.sessions import SESSION_COOKIE, resolve_session
from command_inbox.config import settings
from command_inbox.core.crypto import safe_equal
from command_inbox.core.errors import AppError
from command_inbox.core.events import hub
from command_inbox.core.telemetry import configure_logging, configure_tracing, http_duration, sse_clients
from command_inbox.db.engine import dispose, engine

log = structlog.get_logger(__name__)

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
# Reachable without a session: exact paths, plus the mailbox-OAuth redirect prefix.
PUBLIC_PATHS = {
    "/v1/auth/demo",
    "/v1/auth/login",
    "/v1/auth/oidc/login",
    "/v1/auth/oidc/callback",
    "/v1/intake/messages",
}
PUBLIC_PREFIXES = ("/v1/oauth/",)
# State-changing but authenticated another way (no session yet, or a signed webhook). Exact paths only.
CSRF_EXEMPT = {"/v1/auth/login", "/v1/intake/messages"}


def is_public(path: str) -> bool:
    docs = settings.env != "production" and path in ("/v1/openapi.json", "/v1/docs")
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES) or docs


def problem(status: int, code: str, title: str, request: Request, detail: str | None = None) -> JSONResponse:
    body = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "code": code,
        "requestId": getattr(request.state, "request_id", None),
    }
    if detail:
        body["detail"] = detail
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await _assert_rls_applies()
    await hub.start()
    worker = None
    if settings.embedded_worker and settings.env != "test":
        from command_inbox.worker import build_worker, start_scheduler

        worker = build_worker()
        hub.on_job(worker.poke)
        worker.start()
        scheduler = asyncio.create_task(start_scheduler())
    log.info(
        "command inbox api ready",
        port=settings.port,
        embedded_worker=bool(worker),
        demo=settings.demo_mode,
        sso=settings.oidc_enabled,
    )
    try:
        yield
    finally:
        if worker is not None:
            scheduler.cancel()
            await worker.stop()
        await hub.stop()
        await dispose()


async def _assert_rls_applies() -> None:
    """Refuse to run as a role that bypasses row-level security: tenant isolation would silently vanish."""
    async with engine.connect() as conn:
        bypass = (
            await conn.execute(
                text("select rolsuper or rolbypassrls from pg_roles where rolname = current_user")
            )
        ).scalar()
    if bypass and settings.env == "production":
        raise RuntimeError("the runtime database role bypasses row-level security; connect as the app role")
    if bypass:
        log.warning("runtime database role bypasses RLS: acceptable only in local development")


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="Command Inbox API",
        version="2.0.0",
        lifespan=lifespan,
        description="AI triage and resolution for bank customer mail.",
        openapi_url="/v1/openapi.json",
        docs_url="/v1/docs",
        redoc_url=None,
    )
    configure_tracing(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def identity(request: Request, call_next):  # type: ignore[no-untyped-def]
        started = time.perf_counter()
        rid = request.headers.get("x-request-id", "")[:64] or str(uuid.uuid4())
        request.state.request_id = rid
        structlog.contextvars.bind_contextvars(request_id=rid)
        path = request.url.path
        try:
            token = request.cookies.get(SESSION_COOKIE)
            if token:
                resolved = await resolve_session(token, rid)
                if resolved:
                    request.state.ctx = resolved.ctx
                    request.state.csrf_token = resolved.csrf_token
                    structlog.contextvars.bind_contextvars(
                        tenant_id=resolved.ctx.org_id, user_id=resolved.ctx.user.id
                    )
            ctx = getattr(request.state, "ctx", None)
            if path.startswith("/v1/") and ctx is None and not is_public(path):
                return problem(401, "unauthenticated", "Sign in to continue", request)
            if request.method in UNSAFE and ctx is not None and path not in CSRF_EXEMPT:
                header = request.headers.get("x-csrf-token", "")
                if not header or not safe_equal(header, request.state.csrf_token):
                    return problem(403, "csrf", "Missing or invalid CSRF token", request)
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        route = request.scope.get("route")
        http_duration.labels(
            request.method, getattr(route, "path", "unmatched"), str(response.status_code)
        ).observe(time.perf_counter() - started)
        response.headers["x-request-id"] = rid
        response.headers.setdefault("x-content-type-options", "nosniff")
        response.headers.setdefault("referrer-policy", "same-origin")
        response.headers.setdefault("x-frame-options", "DENY")
        response.headers.setdefault("cache-control", "no-store")
        return response

    @app.exception_handler(AppError)
    async def app_error(request: Request, err: AppError) -> JSONResponse:
        return problem(err.status, err.code, err.title, request, err.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, err: RequestValidationError) -> JSONResponse:
        first = err.errors()[0] if err.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        return problem(
            400,
            "validation",
            "The request is not valid.",
            request,
            f"{where}: {first.get('msg', 'invalid')}" if where else None,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, err: StarletteHTTPException) -> JSONResponse:
        return problem(err.status_code, "http_error", str(err.detail), request)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, err: Exception) -> JSONResponse:
        log.exception("unhandled error", path=request.url.path)
        return problem(500, "internal", "Something went wrong on our side.", request)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> Response:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("select 1"))
        except Exception:
            return JSONResponse({"ok": False}, status_code=503)
        return JSONResponse({"ok": True})

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        sse_clients.set(hub.clients())
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/stream", include_in_schema=False)
    async def stream(request: Request) -> StreamingResponse:
        ctx = request.state.ctx
        queue = hub.subscribe(ctx.org_id)

        async def events() -> AsyncIterator[str]:
            try:
                yield f'event: ready\ndata: {{"orgId":"{ctx.org_id}"}}\n\n'
                while not await request.is_disconnected():
                    try:
                        evt = await asyncio.wait_for(queue.get(), timeout=25)
                        yield f"event: {evt['topic']}\ndata: {json.dumps(evt)}\n\n"
                    except TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                hub.unsubscribe(ctx.org_id, queue)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache, no-transform", "x-accel-buffering": "no"},
        )

    from command_inbox.routers import registry

    for router in registry.routers():
        app.include_router(router)
    return app


def run() -> None:
    import uvicorn

    uvicorn.run(
        "command_inbox.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        log_config=None,
    )
