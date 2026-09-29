"""Delegated OAuth credentials for one mailbox (decision D3).

The token set (refresh token included) is sealed with the tenant's data key and bound to the mailbox row.
An access token is refreshed when it has under five minutes left, under a row lock so concurrent workers
refresh once. A refused refresh (`invalid_grant`: revoked, password reset, 90 days unused, Conditional
Access) puts the mailbox in `reauth_required`: nothing is retried until a person reconnects it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.config import settings
from command_inbox.core.clock import clock
from command_inbox.db.engine import tenant_tx
from command_inbox.db.models import Mailbox, MailSyncEvent
from command_inbox.mail.types import ConnectorError, ReauthRequired
from command_inbox.platform.keys import tenant_decrypt, tenant_encrypt

REFRESH_MARGIN = timedelta(minutes=5)


def token_url(provider: str) -> str:
    if provider == "graph":
        return f"https://login.microsoftonline.com/{settings.ms_tenant}/oauth2/v2.0/token"
    return "https://oauth2.googleapis.com/token"


def client_credentials(provider: str) -> tuple[str, str]:
    if provider == "graph":
        return settings.ms_client_id or "", settings.ms_client_secret or ""
    return settings.google_client_id or "", settings.google_client_secret or ""


def _aad(mailbox_id: str) -> str:
    return f"mailbox-credentials|{mailbox_id}"


async def seal_tokens(
    tx: AsyncSession, org_id: str, mailbox_id: str, tokens: dict[str, Any]
) -> tuple[str, datetime]:
    """Seal a fresh token response; returns (sealed, access-token expiry)."""
    expires_at = clock.now() + timedelta(seconds=int(tokens.get("expires_in", 3600)))
    body = {**tokens, "expires_at": expires_at.isoformat()}
    return await tenant_encrypt(tx, org_id, json.dumps(body), aad=_aad(mailbox_id)), expires_at


@dataclass(slots=True)
class TokenProvider:
    """Hands a valid access token to a connector, refreshing it when due."""

    org_id: str
    mailbox_id: str
    provider: str

    async def __call__(self, *, force: bool = False) -> str:
        revoked = False
        async with tenant_tx(self.org_id) as tx:
            mb = (
                await tx.execute(
                    select(Mailbox)
                    .where(Mailbox.org_id == self.org_id, Mailbox.id == self.mailbox_id)
                    .with_for_update()
                )
            ).scalar_one()
            if mb.connection == "reauth_required":
                raise ReauthRequired("The mailbox must be reconnected.")
            if not mb.credentials_enc or not mb.credentials_enc.startswith("t1."):
                raise ReauthRequired("The mailbox has no stored sign-in.")
            tokens = json.loads(await tenant_decrypt(tx, self.org_id, mb.credentials_enc, aad=_aad(mb.id)))
            expires_at = datetime.fromisoformat(tokens["expires_at"])
            if not force and expires_at - clock.now() > REFRESH_MARGIN:
                return str(tokens["access_token"])
            fresh = await self._refresh(tokens)
            if fresh is None:
                # Recorded and committed before raising: nothing retries until a person reconnects.
                mb.connection = "reauth_required"
                mb.last_error = "The sign-in was revoked or expired. Reconnect the mailbox."
                mb.last_error_at = clock.now()
                tx.add(
                    MailSyncEvent(
                        org_id=self.org_id,
                        mailbox_id=mb.id,
                        kind="token_refresh",
                        ok=False,
                        detail={"error": "invalid_grant"},
                    )
                )
                revoked = True
            else:
                # Providers may rotate the refresh token; keep the old one when they don't send a new one.
                merged = {**tokens, **fresh}
                merged["refresh_token"] = fresh.get("refresh_token") or tokens.get("refresh_token")
                mb.credentials_enc, mb.token_expires_at = await seal_tokens(tx, self.org_id, mb.id, merged)
                tx.add(
                    MailSyncEvent(
                        org_id=self.org_id, mailbox_id=mb.id, kind="token_refresh", ok=True, detail={}
                    )
                )
                access = str(merged["access_token"])
        if revoked:
            raise ReauthRequired("The sign-in was revoked or expired.")
        return access

    async def _refresh(self, tokens: dict[str, Any]) -> dict[str, Any] | None:
        client_id, secret = client_credentials(self.provider)
        data = {
            "grant_type": "refresh_token",
            "refresh_token": tokens.get("refresh_token", ""),
            "client_id": client_id,
            "client_secret": secret,
        }
        if self.provider == "graph":
            data["scope"] = GRAPH_SCOPES
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(token_url(self.provider), data=data)
        if r.status_code == 200:
            return dict(r.json())
        err = (r.json() if r.headers.get("content-type", "").startswith("application/json") else {}).get(
            "error"
        )
        if r.status_code in (400, 401) and err in (
            "invalid_grant",
            "interaction_required",
            "unauthorized_client",
        ):
            return None
        raise ConnectorError(f"token refresh failed ({r.status_code} {err or ''})".strip())


GRAPH_SCOPES = "offline_access https://graph.microsoft.com/User.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Mail.Send"
GMAIL_SCOPES = "https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/gmail.send"
