"""`command-inbox-operator`: manage platform operators from the command line.

    command-inbox-operator add EMAIL --name "Full Name" --role platform_owner
    command-inbox-operator list
    command-inbox-operator disable EMAIL

This is how the first platform owner exists on a new stack (nobody can sign in to the console before it);
after that, operators sign in through the operators' Keycloak realm. Every change is written to the platform
audit chain as done by "cli".
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from command_inbox.core.clock import clock


async def _add(email: str, name: str, role: str) -> int:
    from command_inbox.db.engine import dispose, global_tx
    from command_inbox.db.models import PlatformOperator
    from command_inbox.platform.audit import platform_audit

    try:
        async with global_tx() as g:
            if (await g.execute(select(PlatformOperator).where(PlatformOperator.email == email))).first():
                print(f"{email} is already an operator")
                return 1
            g.add(PlatformOperator(email=email, name=name, role=role))
            await platform_audit(
                g,
                operator_id=None,
                operator_email="cli",
                action="operator.created",
                summary=f"Operator {email} created as {role}",
                data={"email": email, "role": role},
            )
        print(f"added {email} as {role}")
        return 0
    finally:
        await dispose()


async def _list() -> int:
    from command_inbox.db.engine import dispose, global_tx
    from command_inbox.db.models import PlatformOperator

    try:
        async with global_tx() as g:
            for o in (await g.execute(select(PlatformOperator).order_by(PlatformOperator.email))).scalars():
                state = "disabled" if o.disabled_at else "active"
                print(f"{o.email}\t{o.role}\t{state}\t{o.name}")
        return 0
    finally:
        await dispose()


async def _disable(email: str) -> int:
    from command_inbox.db.engine import dispose, global_tx
    from command_inbox.db.models import PlatformOperator, PlatformSession
    from command_inbox.platform.audit import platform_audit

    try:
        async with global_tx() as g:
            op = (
                await g.execute(select(PlatformOperator).where(PlatformOperator.email == email))
            ).scalar_one_or_none()
            if op is None:
                print(f"{email} is not an operator")
                return 1
            now = clock.now()
            op.disabled_at = now
            for s in (
                await g.execute(select(PlatformSession).where(PlatformSession.operator_id == op.id))
            ).scalars():
                s.revoked_at = s.revoked_at or now
            await platform_audit(
                g,
                operator_id=None,
                operator_email="cli",
                action="operator.disabled",
                summary=f"Operator {email} disabled",
                data={"email": email},
            )
        print(f"disabled {email}")
        return 0
    finally:
        await dispose()


async def _keys(action: str, slug: str | None) -> int:
    """Rotate tenant data keys (new version for new data) or re-wrap them under the configured KEK."""
    from command_inbox.db.engine import dispose, global_tx
    from command_inbox.db.models import Org
    from command_inbox.platform.audit import platform_audit
    from command_inbox.platform.keys import rewrap_tenant_keys, rotate_tenant_key

    try:
        async with global_tx() as g:
            q = select(Org).where(Org.status != "archived")
            if slug:
                q = q.where(Org.slug == slug)
            orgs = list((await g.execute(q.order_by(Org.slug))).scalars())
            if not orgs:
                print(f"no tenant {slug!r}" if slug else "no tenants")
                return 2
            for o in orgs:
                if action == "rotate":
                    version = await rotate_tenant_key(g, o.id)
                    detail, data = f"new data key v{version}", {"version": version}
                else:
                    moved = await rewrap_tenant_keys(g, o.id)
                    detail, data = f"{moved} data keys re-wrapped", {"rewrapped": moved}
                await platform_audit(
                    g,
                    operator_id=None,
                    operator_email="cli",
                    action=f"tenant.keys_{action}",
                    summary=f"{o.name}: {detail}",
                    tenant_id=o.id,
                    data=data,
                )
                print(f"{o.slug}: {detail}")
        return 0
    finally:
        await dispose()


async def _mail_catch_up(slug: str | None) -> int:
    """After a restore (DR) or an outage: queue a catch-up for every connected mailbox from its stored cursor.
    The provider mailbox is the source of truth and ingestion is idempotent, so nothing is lost or doubled."""
    from command_inbox.db.engine import dispose, global_tx, tenant_tx
    from command_inbox.db.models import Mailbox, Org
    from command_inbox.mail.sync import enqueue_sync

    try:
        async with global_tx() as g:
            q = select(Org.id, Org.slug).where(Org.status.not_in(("archived", "draft")))
            if slug:
                q = q.where(Org.slug == slug)
            orgs = (await g.execute(q)).all()
        total = 0
        for org_id, org_slug in orgs:
            async with tenant_tx(org_id) as tx:
                boxes = (
                    (
                        await tx.execute(
                            select(Mailbox.id).where(
                                Mailbox.org_id == org_id,
                                Mailbox.connection.in_(("live", "degraded", "syncing")),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for mailbox_id in boxes:
                    await enqueue_sync(tx, org_id, mailbox_id, "catch_up")
            print(f"{org_slug}: {len(boxes)} mailboxes queued for catch-up")
            total += len(boxes)
        return 0 if orgs else 2
    finally:
        await dispose()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="command-inbox-operator", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("email")
    a.add_argument("--name", required=True)
    a.add_argument("--role", choices=["platform_owner", "operator", "support"], required=True)
    sub.add_parser("list")
    d = sub.add_parser("disable")
    d.add_argument("email")
    k = sub.add_parser("keys", help="rotate tenant data keys, or re-wrap them under the configured KEK")
    k.add_argument("action", choices=["rotate", "rewrap"])
    k.add_argument("--tenant", help="tenant slug (default: every tenant)")
    m = sub.add_parser("mail", help="queue a catch-up for every connected mailbox (after a restore)")
    m.add_argument("action", choices=["catch-up"])
    m.add_argument("--tenant", help="tenant slug (default: every tenant)")
    args = p.parse_args(argv)
    if args.cmd == "mail":
        return asyncio.run(_mail_catch_up(args.tenant))
    if args.cmd == "keys":
        return asyncio.run(_keys(args.action, args.tenant))
    if args.cmd == "add":
        return asyncio.run(_add(args.email.strip().lower(), args.name.strip(), args.role))
    if args.cmd == "list":
        return asyncio.run(_list())
    return asyncio.run(_disable(args.email.strip().lower()))


if __name__ == "__main__":
    raise SystemExit(main())
