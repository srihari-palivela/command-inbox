"""Operations a bank's admin controls: retention, audit export (signed), and SIEM streaming of the audit log.

Retention (daily `retention_sweep` job): customer mail text, the original MIME and drafts of tickets closed
longer ago than `retention_mail_days` are replaced by a marker; model traces older than
`retention_trace_days` are deleted. Ticket metadata, decisions and the audit log stay (the audit log is
never removed by retention; its chain would break). Each sweep that removed anything is audited.

Audit export: a zip of `events.csv`, `events.jsonl` and `manifest.json`. The manifest names each file's
SHA-256, the event range and the chain value of the last event, and is signed (HMAC-SHA256) with a key
derived for this workspace; `verify_manifest` checks a manifest handed back later.

SIEM: every minute (`siem_push`), new audit events are POSTed in order, in batches, to the bank's HTTPS
endpoint with `X-CI-Signature: sha256=<hmac of the body>` using the bank's shared secret. Delivery is
at-least-once: the cursor only moves on a 2xx, so a receiver dedupes by `seq`.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import zipfile
from datetime import datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.audit import audit
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import SYSTEM_ACTOR, Ctx, actor_of
from command_inbox.core.crypto import _key, canonical_json, hmac_hex, safe_equal
from command_inbox.core.errors import unprocessable
from command_inbox.core.jobs import JobRow
from command_inbox.db.engine import tenant_tx
from command_inbox.db.models import AuditEvent, Org
from command_inbox.platform.keys import tenant_decrypt, tenant_encrypt
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto
from command_inbox.schemas.requests import OperationsBody

log = structlog.get_logger(__name__)
REDACTED = "[Removed after the retention period]"
SIEM_BATCH = 500
EXPORT_LIMIT = 200_000


# ── Settings ─────────────────────────────────────────────────────────────────────────────────────────────
async def get_operations(tx: AsyncSession, ctx: Ctx) -> dto.OperationsDTO:
    require(ctx, "audit.verify", "view operations settings")
    o = (await tx.execute(select(Org).where(Org.id == ctx.org_id))).scalar_one()
    last = (
        await tx.execute(select(func.coalesce(func.max(AuditEvent.seq), 0)).where(AuditEvent.org_id == o.id))
    ).scalar_one()
    count = (
        await tx.execute(select(func.count()).select_from(AuditEvent).where(AuditEvent.org_id == o.id))
    ).scalar_one()
    pending = (
        await tx.execute(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.org_id == o.id, AuditEvent.seq > o.siem_cursor)
        )
    ).scalar_one()
    return dto.OperationsDTO(
        retention_mail_days=o.retention_mail_days,
        retention_trace_days=o.retention_trace_days,
        siem=dto.OperationsDTOSiem(
            url=o.siem_url,
            has_secret=bool(o.siem_secret_sealed),
            delivered_seq=min(o.siem_cursor, last),
            pending=pending if o.siem_url else 0,
            last_ok_at=iso_ms(o.siem_last_ok_at) if o.siem_last_ok_at else None,
            last_error=o.siem_last_error,
        ),
        audit_events=count,
        can_edit=ctx.can("workspace.manage"),
    )


async def update_operations(tx: AsyncSession, ctx: Ctx, body: OperationsBody) -> dto.OperationsDTO:
    require(ctx, "workspace.manage", "change retention and SIEM settings")
    o = (await tx.execute(select(Org).where(Org.id == ctx.org_id).with_for_update())).scalar_one()
    if body.siem_url and not (body.siem_secret or o.siem_secret_sealed):
        raise unprocessable("siem_secret_required", "Set a signing secret for the SIEM endpoint.")
    before = {
        "retentionMailDays": o.retention_mail_days,
        "retentionTraceDays": o.retention_trace_days,
        "siemUrl": o.siem_url,
    }
    o.retention_mail_days = body.retention_mail_days
    o.retention_trace_days = body.retention_trace_days
    if body.siem_url != o.siem_url:
        o.siem_last_error = None
        if body.siem_url and not o.siem_url:
            # A new stream starts from the next event; the history is available as an export.
            o.siem_cursor = (
                await tx.execute(
                    select(func.coalesce(func.max(AuditEvent.seq), 0)).where(AuditEvent.org_id == o.id)
                )
            ).scalar_one()
    o.siem_url = body.siem_url
    if body.siem_secret:
        o.siem_secret_sealed = await tenant_encrypt(tx, o.id, body.siem_secret, aad=f"siem:{o.id}")
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="workspace.operations_changed",
        entity="org",
        entity_id=ctx.org_id,
        summary=f"{ctx.user.name} changed retention and SIEM settings",
        data={
            "before": before,
            "after": {
                "retentionMailDays": o.retention_mail_days,
                "retentionTraceDays": o.retention_trace_days,
                "siemUrl": o.siem_url,
                "secretChanged": bool(body.siem_secret),
            },
        },
    )
    return await get_operations(tx, ctx)


# ── Retention ────────────────────────────────────────────────────────────────────────────────────────────
_REDACT_MESSAGES = (
    "update messages set body = :marker, redacted_at = :now where org_id = :org and redacted_at is null "
    "and ticket_id in (select id from tickets where org_id = :org and status in ('resolved', 'closed') and coalesce(closed_at, resolved_at) < :cutoff)"
)
_REDACT_MAIL = (
    "update mail_messages set body_text = '', raw_sealed = null, redacted_at = :now where org_id = :org "
    "and redacted_at is null and ticket_id in (select id from tickets where org_id = :org and status in ('resolved', 'closed') and coalesce(closed_at, resolved_at) < :cutoff)"
)
_REDACT_DRAFTS = (
    "update drafts set original_body = :marker, current_body = :marker where org_id = :org "
    "and current_body <> :marker and ticket_id in (select id from tickets where org_id = :org and status in ('resolved', 'closed') and coalesce(closed_at, resolved_at) < :cutoff)"
)


async def apply_retention(tx: AsyncSession, org_id: str, now: datetime | None = None) -> dict[str, int]:
    now = now or clock.now()
    o = (await tx.execute(select(Org).where(Org.id == org_id))).scalar_one()
    out = {"messages": 0, "mail": 0, "drafts": 0, "spans": 0}
    if o.retention_mail_days:
        p = {
            "org": org_id,
            "cutoff": now - timedelta(days=o.retention_mail_days),
            "marker": REDACTED,
            "now": now,
        }
        out["messages"] = (
            await tx.execute(
                text(_REDACT_MESSAGES),
                p,
            )
        ).rowcount
        out["mail"] = (
            await tx.execute(
                text(_REDACT_MAIL),
                p,
            )
        ).rowcount
        out["drafts"] = (
            await tx.execute(
                text(_REDACT_DRAFTS),
                p,
            )
        ).rowcount
    if o.retention_trace_days:
        out["spans"] = (
            await tx.execute(
                text(
                    "delete from trace_spans where org_id = :org and run_id in "
                    "(select id from triage_runs where org_id = :org and created_at < :cutoff)"
                ),
                {"org": org_id, "cutoff": now - timedelta(days=o.retention_trace_days)},
            )
        ).rowcount
    if any(out.values()):
        await audit(
            tx,
            org_id,
            actor=SYSTEM_ACTOR,
            action="retention.applied",
            entity="org",
            entity_id=org_id,
            summary=f"Retention removed the text of {out['messages'] + out['mail']} messages and "
            f"{out['drafts']} drafts, and {out['spans']} model trace steps",
            data={**out, "mailDays": o.retention_mail_days, "traceDays": o.retention_trace_days},
        )
    return out


async def run_retention_sweep(job: JobRow) -> None:
    async with tenant_tx(job.org_id) as tx:
        out = await apply_retention(tx, job.org_id)
    if any(out.values()):
        log.info("retention applied", org_id=job.org_id, **out)


# ── Audit export ─────────────────────────────────────────────────────────────────────────────────────────
FIELDS = ("seq", "id", "at", "actorKind", "actorName", "action", "entity", "entityId", "ticketId", "summary")


def _event_json(e: AuditEvent) -> dict[str, Any]:
    return {
        "seq": e.seq,
        "id": e.id,
        "at": iso_ms(e.at),
        "actorKind": e.actor_kind,
        "actorName": e.actor_name,
        "action": e.action,
        "entity": e.entity,
        "entityId": e.entity_id,
        "ticketId": e.ticket_id,
        "summary": e.summary,
        "data": e.data or {},
        "prevHash": e.prev_hash,
        "hash": e.hash,
    }


def _sign(org_id: str, manifest: dict[str, Any]) -> str:
    body = canonical_json({k: v for k, v in manifest.items() if k != "signature"})
    return hmac.new(_key(f"audit-export:{org_id}"), body.encode(), hashlib.sha256).hexdigest()


def verify_manifest(org_id: str, manifest: dict[str, Any]) -> bool:
    return safe_equal(_sign(org_id, manifest), str(manifest.get("signature", "")))


async def export_audit(
    tx: AsyncSession, ctx: Ctx, since: datetime | None, until: datetime | None
) -> tuple[bytes, dict[str, Any]]:
    require(ctx, "audit.verify", "export the audit log")
    q = select(AuditEvent).where(AuditEvent.org_id == ctx.org_id)
    if since:
        q = q.where(AuditEvent.at >= since)
    if until:
        q = q.where(AuditEvent.at < until)
    events = list((await tx.execute(q.order_by(AuditEvent.seq).limit(EXPORT_LIMIT))).scalars())
    rows = [_event_json(e) for e in events]
    csv_buf = io.StringIO()
    w = csv.writer(csv_buf)
    w.writerow([*FIELDS, "data", "hash"])
    for r in rows:
        w.writerow([r[f] if r[f] is not None else "" for f in FIELDS] + [json.dumps(r["data"]), r["hash"]])
    files = {
        "events.csv": csv_buf.getvalue().encode(),
        "events.jsonl": "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode(),
    }
    slug = (await tx.execute(select(Org.slug).where(Org.id == ctx.org_id))).scalar_one()
    manifest: dict[str, Any] = {
        "tenant": slug,
        "generatedAt": iso_ms(clock.now()),
        "since": iso_ms(since) if since else None,
        "until": iso_ms(until) if until else None,
        "events": len(rows),
        "firstSeq": rows[0]["seq"] if rows else None,
        "lastSeq": rows[-1]["seq"] if rows else None,
        "lastHash": rows[-1]["hash"] if rows else None,
        "files": [
            {"name": n, "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)} for n, b in files.items()
        ],
    }
    manifest["signature"] = _sign(ctx.org_id, manifest)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in files.items():
            z.writestr(n, b)
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="audit.exported",
        entity="org",
        entity_id=ctx.org_id,
        summary=f"{ctx.user.name} exported {len(rows)} audit events",
        data={k: manifest[k] for k in ("since", "until", "events", "firstSeq", "lastSeq")},
    )
    return buf.getvalue(), manifest


# ── SIEM streaming ───────────────────────────────────────────────────────────────────────────────────────
async def push_siem(org_id: str, client: httpx.AsyncClient | None = None) -> int:
    """Deliver the next batch; returns the number of events delivered (0 when nothing to do or it failed)."""
    async with tenant_tx(org_id) as tx:
        o = (await tx.execute(select(Org).where(Org.id == org_id))).scalar_one()
        if not o.siem_url or not o.siem_secret_sealed:
            return 0
        events = list(
            (
                await tx.execute(
                    select(AuditEvent)
                    .where(AuditEvent.org_id == org_id, AuditEvent.seq > o.siem_cursor)
                    .order_by(AuditEvent.seq)
                    .limit(SIEM_BATCH)
                )
            ).scalars()
        )
        if not events:
            return 0
        secret = await tenant_decrypt(tx, org_id, o.siem_secret_sealed, aad=f"siem:{org_id}")
        url, slug = o.siem_url, o.slug
    body = json.dumps(
        {"tenant": slug, "events": [_event_json(e) for e in events]}, ensure_ascii=False
    ).encode()
    headers = {
        "content-type": "application/json",
        "x-ci-signature": f"sha256={hmac_hex(secret, body)}",
        "x-ci-tenant": slug,
        "x-ci-last-seq": str(events[-1].seq),
    }
    error: str | None = None
    try:
        own = client is None
        http = client or httpx.AsyncClient(timeout=10.0)
        try:
            r = await http.post(url, content=body, headers=headers)
        finally:
            if own:
                await http.aclose()
        if not 200 <= r.status_code < 300:
            error = f"HTTP {r.status_code}"
    except httpx.HTTPError as err:
        error = f"{type(err).__name__}: {err}"[:300]
    async with tenant_tx(org_id) as tx:
        o = (await tx.execute(select(Org).where(Org.id == org_id).with_for_update())).scalar_one()
        if error is None:
            o.siem_cursor = max(o.siem_cursor, events[-1].seq)
            o.siem_last_ok_at, o.siem_last_error = clock.now(), None
        else:
            o.siem_last_error = error
    if error:
        log.warning("siem delivery failed", org_id=org_id, err=error)
        return 0
    return len(events)


async def run_siem_push(job: JobRow) -> None:
    await push_siem(job.org_id)
