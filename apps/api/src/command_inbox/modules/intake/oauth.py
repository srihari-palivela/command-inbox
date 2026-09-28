"""Mailbox OAuth (authorisation-code flow with PKCE). Read-only mail scopes: the AI never holds send scope —
replies go out under a named approver.

Hardening over the previous service:
- **State bound to the session.** `state` is sealed with `core.crypto.encrypt(purpose="mailbox-oauth")`
  and the associated data includes a hash of the signed-in session id and the provider. A callback that
  arrives in another browser session (login CSRF / session swapping), for another provider, another
  tenant, or after 10 minutes is refused (`bad_state` / `expired_state`). The callback therefore needs the
  same signed-in session that started the flow.
- **PKCE (S256).** The code verifier travels only inside the sealed state (it is never readable by the
  browser or the provider) and is sent with the code exchange.
- **The connected account must be the mailbox.** After the exchange we ask the provider which account
  granted access and refuse (`mailbox_mismatch`, 422) unless it equals the mailbox address; no tokens are
  stored in that case.
Tokens are encrypted at rest (purpose "mailbox-credentials", bound to the tenant and mailbox row).
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
from cryptography.exceptions import InvalidTag
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select, update

from command_inbox.config import settings
from command_inbox.core.audit import audit
from command_inbox.core.clock import clock
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.crypto import decrypt, encrypt, random_token, sha256
from command_inbox.core.errors import bad_request, not_found, unprocessable
from command_inbox.db.engine import tenant_tx
from command_inbox.db.models import Mailbox
from command_inbox.rbac.policy import require

OAuthProvider = Literal["microsoft", "google"]
STATE_TTL_MS = 10 * 60_000
STATE_PURPOSE = "mailbox-oauth"
CREDENTIALS_PURPOSE = "mailbox-credentials"


class _OAuthSettings(BaseSettings):
    """Provider app registrations. (Belongs in `config.Settings`; kept here until that file is edited.)"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    ms_client_id: str | None = None
    ms_client_secret: str | None = None
    google_client_id: str | None = None
    google_client_secret: str | None = None


oauth_settings = _OAuthSettings()


@dataclass(frozen=True, slots=True)
class _Provider:
    authorize: str
    token: str
    scope: str
    extra: dict[str, str]

    def client_id(self, p: str) -> str | None:
        return oauth_settings.ms_client_id if p == "microsoft" else oauth_settings.google_client_id

    def secret(self, p: str) -> str | None:
        return oauth_settings.ms_client_secret if p == "microsoft" else oauth_settings.google_client_secret


PROVIDERS: dict[str, _Provider] = {
    "microsoft": _Provider(
        authorize="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token="https://login.microsoftonline.com/common/oauth2/v2.0/token",  # noqa: S106 - a URL
        # User.Read lets us confirm which account granted access (the mailbox-address check).
        scope="offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read",
        extra={"prompt": "consent"},
    ),
    "google": _Provider(
        authorize="https://accounts.google.com/o/oauth2/v2/auth",
        token="https://oauth2.googleapis.com/token",  # noqa: S106 - a URL
        scope="https://www.googleapis.com/auth/gmail.readonly",
        extra={"access_type": "offline", "prompt": "consent"},
    ),
}


def is_configured(p: str) -> bool:
    cfg = PROVIDERS.get(p)
    return bool(cfg and cfg.client_id(p) and cfg.secret(p))


def redirect_uri(p: str) -> str:
    return f"{settings.public_api_url.rstrip('/')}/v1/oauth/{p}/callback"


def _state_aad(p: str, session_id: str) -> str:
    return f"{p}|{sha256(session_id)}"


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def authorize_url(ctx: Ctx, p: str, mailbox_id: str) -> str:
    """The provider consent URL for connecting `mailbox_id`, bound to the caller's session.

    Callers must have checked that the mailbox belongs to `ctx.org_id` (e.g. they just created it)."""
    if not is_configured(p):
        raise unprocessable("oauth_not_configured", f"{p} OAuth is not configured for this deployment.")
    cfg = PROVIDERS[p]
    verifier = random_token(48)
    state = encrypt(
        json.dumps(
            {
                "o": ctx.org_id,
                "m": mailbox_id,
                "p": p,
                "v": verifier,
                "exp": int(clock.now().timestamp() * 1000) + STATE_TTL_MS,
            }
        ),
        purpose=STATE_PURPOSE,
        aad=_state_aad(p, ctx.session_id),
    )
    params = {
        "client_id": cfg.client_id(p) or "",
        "response_type": "code",
        "redirect_uri": redirect_uri(p),
        "scope": cfg.scope,
        "state": state,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
        **cfg.extra,
    }
    return f"{cfg.authorize}?{urlencode(params)}"


def _open_state(ctx: Ctx, p: str, raw: str) -> dict[str, Any]:
    try:
        st = json.loads(decrypt(raw, purpose=STATE_PURPOSE, aad=_state_aad(p, ctx.session_id)))
    except (InvalidTag, ValueError, IndexError, TypeError, UnicodeDecodeError) as err:
        raise bad_request("bad_state", "Invalid OAuth state.") from err
    if not isinstance(st, dict) or st.get("p") != p or st.get("o") != ctx.org_id:
        raise bad_request("bad_state", "Invalid OAuth state.")
    if int(st.get("exp", 0)) < clock.now().timestamp() * 1000:
        raise bad_request("expired_state", "The connection attempt expired. Start again.")
    return st


async def exchange_code(p: str, code: str, verifier: str) -> dict[str, Any]:
    """Code (+ PKCE verifier) → tokens. Replaced in tests."""
    cfg = PROVIDERS[p]
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.post(
            cfg.token,
            data={
                "client_id": cfg.client_id(p) or "",
                "client_secret": cfg.secret(p) or "",
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri(p),
                "code_verifier": verifier,
            },
        )
    if res.status_code != 200:
        raise bad_request("token_exchange_failed", f"The {p} token exchange failed ({res.status_code}).")
    return dict(res.json())


async def account_email(p: str, tokens: dict[str, Any]) -> str:
    """Which mailbox granted access, as the provider reports it. Replaced in tests."""
    headers = {"authorization": f"Bearer {tokens.get('access_token', '')}"}
    url = (
        "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName"
        if p == "microsoft"
        else "https://gmail.googleapis.com/gmail/v1/users/me/profile"
    )
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(url, headers=headers)
    if res.status_code != 200:
        raise bad_request("account_lookup_failed", f"Could not confirm the {p} account ({res.status_code}).")
    body = res.json()
    email = (
        body.get("mail") or body.get("userPrincipalName") if p == "microsoft" else body.get("emailAddress")
    )
    return str(email or "")


async def handle_callback(ctx: Ctx, p: str, code: str, raw_state: str) -> None:
    """Verify the session-bound state, exchange the code, check the account, store encrypted tokens."""
    st = _open_state(ctx, p, raw_state)
    require(ctx, "setup.edit", "connect mailboxes")
    async with tenant_tx(ctx.org_id) as tx:
        mb = (
            await tx.execute(select(Mailbox).where(Mailbox.org_id == ctx.org_id, Mailbox.id == st["m"]))
        ).scalar_one_or_none()
        if mb is None:
            raise not_found("Mailbox")
        address = mb.address
    tokens = await exchange_code(p, code, str(st["v"]))
    account = (await account_email(p, tokens)).strip().lower()
    if account != address.lower():
        raise unprocessable(
            "mailbox_mismatch",
            f"You granted access as {account or 'an unknown account'}, but this board reads {address}. "
            "Sign in as that mailbox and try again.",
        )
    async with tenant_tx(ctx.org_id) as tx:
        await tx.execute(
            update(Mailbox)
            .where(Mailbox.org_id == ctx.org_id, Mailbox.id == st["m"])
            .values(
                credentials_enc=encrypt(
                    json.dumps(tokens), purpose=CREDENTIALS_PURPOSE, aad=f"{ctx.org_id}|mailboxes|{st['m']}"
                ),
                state="streaming",
                last_sync_at=clock.now(),
            )
        )
        await audit(
            tx,
            ctx.org_id,
            actor=actor_of(ctx),
            action="mailbox.connected",
            entity="mailbox",
            entity_id=st["m"],
            summary=f"{ctx.user.name} connected {address} over {p}",
            data={"provider": p},
        )


async def connect_url(ctx: Ctx, mailbox_id: str) -> str:
    """(Re)connect an existing mailbox of this tenant."""
    require(ctx, "setup.edit", "connect mailboxes")
    async with tenant_tx(ctx.org_id) as tx:
        provider = (
            await tx.execute(
                select(Mailbox.provider).where(Mailbox.org_id == ctx.org_id, Mailbox.id == mailbox_id)
            )
        ).scalar_one_or_none()
    if provider is None:
        raise not_found("Mailbox")
    if provider not in PROVIDERS:
        raise unprocessable("oauth_not_supported", f"{provider} mailboxes connect without OAuth.")
    return authorize_url(ctx, provider, mailbox_id)
