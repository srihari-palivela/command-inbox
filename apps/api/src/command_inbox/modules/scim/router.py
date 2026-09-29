"""SCIM 2.0 endpoints at /scim/v2 (bearer token from the workspace's SCIM settings; no session, no CSRF)."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text, update

from command_inbox.core.clock import clock
from command_inbox.core.crypto import sha256
from command_inbox.db.engine import global_tx, tenant_tx
from command_inbox.db.models import ScimToken
from command_inbox.modules.scim import service as s
from command_inbox.modules.scim.service import ScimError

router = APIRouter(prefix="/scim/v2", tags=["scim"], include_in_schema=False)
MEDIA = "application/scim+json"
FILTER = re.compile(r'^\s*(\w+)\s+eq\s+"([^"]*)"\s*$', re.IGNORECASE)


def _json(body: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, media_type=MEDIA)


async def _org(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise ScimError(401, "A bearer token is required.")
    h = sha256(auth[7:].strip())
    async with global_tx() as g:
        org_id = (await g.execute(text("select ci_scim_org(:h)"), {"h": h})).scalar_one_or_none()
    if org_id is None:
        raise ScimError(401, "The token is not valid.")
    org_id = str(org_id)
    async with tenant_tx(org_id) as tx:
        await tx.execute(update(ScimToken).where(ScimToken.token_hash == h).values(last_used_at=clock.now()))
    return org_id


async def _run(request: Request, fn: Callable[[Any, str], Awaitable[Any]], status: int = 200) -> Response:
    try:
        org_id = await _org(request)
        async with tenant_tx(org_id) as tx:
            out = await fn(tx, org_id)
    except ScimError as err:
        return _json(err.body(), err.status)
    if out is None:
        return Response(status_code=204)
    return _json(out, status)


def _paging(request: Request) -> tuple[tuple[str, str] | None, int, int]:
    q = request.query_params
    flt = None
    if q.get("filter"):
        m = FILTER.match(q["filter"])
        if not m:
            raise ScimError(400, "Only 'attribute eq \"value\"' filters are supported.", "invalidFilter")
        flt = (m.group(1).lower(), m.group(2))
    try:
        start = max(1, int(q.get("startIndex", 1)))
        count = max(0, min(int(q.get("count", 100)), 200))
    except ValueError:
        raise ScimError(400, "startIndex and count must be numbers.", "invalidValue") from None
    return flt, start, count


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        raise ScimError(400, "The body is not JSON.", "invalidSyntax") from None
    if not isinstance(body, dict):
        raise ScimError(400, "The body must be a JSON object.", "invalidSyntax")
    return body


@router.get("/ServiceProviderConfig")
async def service_provider_config() -> Response:
    return _json(
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": True},
            "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
            "filter": {"supported": True, "maxResults": 200},
            "changePassword": {"supported": False},
            "sort": {"supported": False},
            "etag": {"supported": False},
            "authenticationSchemes": [
                {"type": "oauthbearertoken", "name": "Bearer token", "description": "Workspace SCIM token"}
            ],
        }
    )


@router.get("/ResourceTypes")
async def resource_types() -> Response:
    return _json(
        {
            "schemas": [s.LIST],
            "totalResults": 2,
            "Resources": [
                {"id": "User", "name": "User", "endpoint": "/Users", "schema": s.USER},
                {"id": "Group", "name": "Group", "endpoint": "/Groups", "schema": s.GROUP},
            ],
        }
    )


@router.get("/Users")
async def users(request: Request) -> Response:
    async def fn(tx: Any, org: str) -> Any:
        flt, start, count = _paging(request)
        return await s.list_users(tx, org, flt, start, count)

    return await _run(request, fn)


@router.post("/Users")
async def create_user(request: Request) -> Response:
    async def fn(tx: Any, org: str) -> Any:
        return await s.create_user(tx, org, await _body(request))

    return await _run(request, fn, 201)


@router.get("/Users/{user_id}")
async def get_user(user_id: str, request: Request) -> Response:
    return await _run(request, lambda tx, org: s.get_user(tx, org, user_id))


@router.put("/Users/{user_id}")
async def put_user(user_id: str, request: Request) -> Response:
    async def fn(tx: Any, org: str) -> Any:
        return await s.replace_user(tx, org, user_id, await _body(request))

    return await _run(request, fn)


@router.patch("/Users/{user_id}")
async def patch_user(user_id: str, request: Request) -> Response:
    async def fn(tx: Any, org: str) -> Any:
        return await s.patch_user(tx, org, user_id, await _body(request))

    return await _run(request, fn)


@router.delete("/Users/{user_id}")
async def delete_user(user_id: str, request: Request) -> Response:
    async def fn(tx: Any, org: str) -> None:
        await s.get_user(tx, org, user_id)  # 404 when not a member
        await s.deprovision(tx, org, user_id)

    return await _run(request, fn)


@router.get("/Groups")
async def groups(request: Request) -> Response:
    async def fn(tx: Any, org: str) -> Any:
        flt, start, count = _paging(request)
        return await s.list_groups(tx, org, flt, start, count)

    return await _run(request, fn)


@router.post("/Groups")
async def create_group(request: Request) -> Response:
    async def fn(tx: Any, org: str) -> Any:
        return await s.create_group(tx, org, await _body(request))

    return await _run(request, fn, 201)


@router.get("/Groups/{group_id}")
async def get_group(group_id: str, request: Request) -> Response:
    async def fn(tx: Any, org: str) -> Any:
        return await s.group_resource(tx, await s._group(tx, org, group_id))

    return await _run(request, fn)


@router.patch("/Groups/{group_id}")
async def patch_group(group_id: str, request: Request) -> Response:
    async def fn(tx: Any, org: str) -> Any:
        return await s.patch_group(tx, org, group_id, await _body(request))

    return await _run(request, fn)


@router.put("/Groups/{group_id}")
async def put_group(group_id: str, request: Request) -> Response:
    async def fn(tx: Any, org: str) -> Any:
        return await s.replace_group(tx, org, group_id, await _body(request))

    return await _run(request, fn)


@router.delete("/Groups/{group_id}")
async def delete_group(group_id: str, request: Request) -> Response:
    return await _run(request, lambda tx, org: s.delete_group(tx, org, group_id))
