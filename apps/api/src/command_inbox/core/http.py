"""Request plumbing shared by every router: identity, tenant transactions, idempotency."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from fastapi import Request, Response
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.context import Ctx
from command_inbox.core.crypto import canonical_json, sha256
from command_inbox.core.errors import conflict, unauthorized, unprocessable
from command_inbox.db.engine import tenant_tx
from command_inbox.db.models import IdempotencyKey

T = TypeVar("T")


def ctx_of(request: Request) -> Ctx:
    ctx = getattr(request.state, "ctx", None)
    if ctx is None:
        raise unauthorized()
    return ctx


async def current_ctx(request: Request) -> Ctx:
    """FastAPI dependency: the signed-in context (401 otherwise)."""
    return ctx_of(request)


async def in_tenant[T](ctx: Ctx, fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run `fn` inside one transaction bound to the caller's tenant."""
    async with tenant_tx(ctx.org_id) as tx:
        return await fn(tx)


async def idempotent[T](
    request: Request, response: Response, ctx: Ctx, body: Any, fn: Callable[[AsyncSession], Awaitable[T]]
) -> T | Any:
    """Honour an `Idempotency-Key` header: a retry with the same key and body returns the stored answer.

    The key row is written in the same transaction as the effect, so a crash cannot leave one without the
    other; a key reused with a different body is a 409.
    """
    key = request.headers.get("idempotency-key")
    if not key:
        return await in_tenant(ctx, fn)
    if len(key) > 200:
        raise unprocessable("bad_idempotency_key", "Idempotency-Key is too long.")
    route = f"{request.method} {request.scope.get('route').path if request.scope.get('route') else request.url.path}"
    request_hash = sha256(canonical_json({"route": route, "params": dict(request.path_params), "body": body}))
    async with tenant_tx(ctx.org_id) as tx:
        inserted = (
            await tx.execute(
                insert(IdempotencyKey)
                .values(
                    org_id=ctx.org_id,
                    user_id=ctx.user.id,
                    key=key,
                    route=route,
                    request_hash=request_hash,
                    status_code=0,
                    response=None,
                )
                .on_conflict_do_nothing()
                .returning(IdempotencyKey.key)
            )
        ).first()
        where = (
            IdempotencyKey.org_id == ctx.org_id,
            IdempotencyKey.user_id == ctx.user.id,
            IdempotencyKey.key == key,
        )
        if inserted is None:
            prev = (await tx.execute(select(IdempotencyKey).where(*where))).scalar_one_or_none()
            if prev is not None and prev.request_hash != request_hash:
                raise conflict(
                    "idempotency_mismatch", "This Idempotency-Key was used for a different request."
                )
            if prev is not None and prev.status_code:
                response.headers["idempotent-replay"] = "true"
                return prev.response
            raise conflict("in_progress", "The same request is already being processed.")
        result = await fn(tx)
        stored = result.model_dump(mode="json", by_alias=True) if hasattr(result, "model_dump") else result
        await tx.execute(update(IdempotencyKey).where(*where).values(status_code=200, response=stored))
        return result
