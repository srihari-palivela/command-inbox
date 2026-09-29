"""Intake routes: the signed provider webhook, the demo mail simulator and mailbox OAuth.

`POST /v1/intake/messages` is public and CSRF-exempt (see `main.py`); it is authenticated by an HMAC over
the raw body and a timestamp (`security.verify_webhook`). The OAuth callback path is public so the provider
redirect reaches it, but `handle_callback` still requires the signed-in session its state is bound to.
"""

from __future__ import annotations

import random
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select

from command_inbox.config import settings
from command_inbox.core.clock import clock
from command_inbox.core.context import Ctx
from command_inbox.core.crypto import sha256
from command_inbox.core.errors import forbidden
from command_inbox.core.http import current_ctx
from command_inbox.db.engine import tenant_tx
from command_inbox.db.models import Mailbox
from command_inbox.modules.intake import oauth, service
from command_inbox.modules.intake.schemas import (
    AuthorizeUrlResult,
    IngestResult,
    IntakeWebhookBody,
    SimulateMailBody,
)
from command_inbox.modules.intake.security import (
    SIGNATURE_HEADER,
    SIMULATED,
    TIMESTAMP_HEADER,
    parse_authentication_results,
    verify_webhook,
)
from command_inbox.rbac.policy import require
from command_inbox.schemas.requests import UUID_RE, IntakeMessageBody

router = APIRouter(prefix="/v1", tags=["intake"])


@router.post("/intake/messages", response_model=IngestResult)
async def intake_message(request: Request) -> IngestResult:
    """Provider push / relay endpoint. Signature first, over the exact bytes received; parse only after."""
    raw = await request.body()
    verify_webhook(raw, request.headers.get(TIMESTAMP_HEADER), request.headers.get(SIGNATURE_HEADER))
    try:
        body = IntakeWebhookBody.model_validate_json(raw)
    except ValidationError as err:
        raise RequestValidationError(err.errors()) from err
    auth_results = body.authentication_results
    source = "payload"
    if not auth_results:
        auth_results, source = request.headers.get("authentication-results"), "header"
    sender = parse_authentication_results(auth_results, body.from_email, source)
    # Without a provider message id, an exact replay inside the window dedupes on the signature instead.
    derived = f"wh-{sha256(request.headers.get(SIGNATURE_HEADER, ''))[:32]}"
    return await service.ingest(
        IntakeMessageBody.model_validate(body.model_dump(exclude={"authentication_results"})),
        sender,
        provider_id=derived,
    )


@router.post("/dev/simulate-mail", response_model=IngestResult)
async def simulate_mail(body: SimulateMailBody, ctx: Ctx = Depends(current_ctx)) -> IngestResult:
    """Demo: deliver a sample email to this workspace, as if it had just arrived."""
    if not settings.demo_mode:
        raise forbidden("Only available in demo mode.")
    require(ctx, "ticket.work", "simulate mail")
    samples = service.SAMPLE_MAILS
    i = body.index if body.index is not None else random.randrange(len(samples))  # noqa: S311 - demo only
    sample = samples[i % len(samples)]
    # Deliver to the sample's mailbox when this workspace has it, else to the workspace's first mailbox.
    async with tenant_tx(ctx.org_id) as tx:
        addresses = list(
            (
                await tx.execute(
                    select(Mailbox.address).where(Mailbox.org_id == ctx.org_id).order_by(Mailbox.sort)
                )
            )
            .scalars()
            .all()
        )
    mailbox = (
        sample["mailbox"]
        if sample["mailbox"] in addresses
        else (addresses[0] if addresses else sample["mailbox"])
    )
    msg = IntakeMessageBody.model_validate(
        {**sample, "mailbox": mailbox, "messageId": f"sim-{int(clock.now().timestamp() * 1000)}-{i}"}
    )
    return await service.ingest(msg, SIMULATED, org_id=ctx.org_id)


@router.post("/mailboxes/{id}/connect", response_model=AuthorizeUrlResult)
async def connect_mailbox(
    id: Annotated[str, Path(pattern=UUID_RE)], ctx: Ctx = Depends(current_ctx)
) -> AuthorizeUrlResult:
    """Start (or redo) the OAuth grant for an existing Microsoft 365 / Google Workspace mailbox."""
    return AuthorizeUrlResult(authorize_url=await oauth.connect_url(ctx, id))


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: Literal["microsoft", "google"],
    code: Annotated[str, Query(max_length=4096)],
    state: Annotated[str, Query(max_length=4096)],
    ctx: Ctx = Depends(current_ctx),
) -> RedirectResponse:
    await oauth.handle_callback(ctx, provider, code, state)
    return RedirectResponse(f"{settings.web_origin}/boards?connected=1", status_code=302)
