"""Deployment rollout states, eval integrity, composite tenant keys and the default deployment backfill.

- Deployment versions move draft → shadow → canary (a share of mail) → published → retired, record who
  last edited them (four-eyes publishing) and the hash of their config.
- Eval runs are bound to the config hash they scored and to a hash of the frozen dataset (case ids, splits,
  inputs and labels); cases carry a calibration/test split and are archived, never deleted.
- Every tenant row that points at another tenant row does so through a composite key (org_id, id), so a
  row can never reference another tenant's data, whatever the application does.
- A tenant can never be left without an admin (deferred constraint trigger on memberships).
- Intake records the sender's DKIM/SPF/DMARC verdict; tickets record whether the sender was verified.
- Every existing tenant gets a default deployment built from its current setup, published as version 1 and
  bound to all its mailboxes and tickets, so no ticket is left without a version.

Revision ID: 0004_rollout_and_integrity
Revises: 0003_security_hardening
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_rollout_and_integrity"
down_revision = "0003_security_hardening"
branch_labels = None
depends_on = None

# (table, constraint name) for the (org_id, id) keys composite foreign keys point at.
ORG_KEYS = (
    ("deployments", "deployments_org_id_id_uq"),
    ("deployment_versions", "deployment_versions_org_id_id_uq"),
    ("eval_datasets", "eval_datasets_org_id_id_uq"),
    ("eval_cases", "eval_cases_org_id_id_uq"),
    ("eval_runs", "eval_runs_org_id_id_uq"),
)

# (name, table, local columns, referred table, referred columns, on delete)
FKS: tuple[tuple[str, str, list[str], str, list[str], str | None], ...] = (
    ("deployments_org_fk", "deployments", ["org_id"], "orgs", ["id"], None),
    (
        "deployments_active_version_fk",
        "deployments",
        ["org_id", "active_version_id"],
        "deployment_versions",
        ["org_id", "id"],
        None,
    ),
    (
        "deployment_versions_deployment_fk",
        "deployment_versions",
        ["org_id", "deployment_id"],
        "deployments",
        ["org_id", "id"],
        None,
    ),
    (
        "deployment_versions_eval_run_fk",
        "deployment_versions",
        ["org_id", "eval_run_id"],
        "eval_runs",
        ["org_id", "id"],
        None,
    ),
    ("eval_datasets_org_fk", "eval_datasets", ["org_id"], "orgs", ["id"], None),
    (
        "eval_datasets_deployment_fk",
        "eval_datasets",
        ["org_id", "deployment_id"],
        "deployments",
        ["org_id", "id"],
        None,
    ),
    (
        "eval_cases_dataset_fk",
        "eval_cases",
        ["org_id", "dataset_id"],
        "eval_datasets",
        ["org_id", "id"],
        "CASCADE",
    ),
    ("eval_runs_dataset_fk", "eval_runs", ["org_id", "dataset_id"], "eval_datasets", ["org_id", "id"], None),
    (
        "eval_runs_version_fk",
        "eval_runs",
        ["org_id", "deployment_version_id"],
        "deployment_versions",
        ["org_id", "id"],
        None,
    ),
    ("eval_results_run_fk", "eval_results", ["org_id", "run_id"], "eval_runs", ["org_id", "id"], "CASCADE"),
    ("eval_results_case_fk", "eval_results", ["org_id", "case_id"], "eval_cases", ["org_id", "id"], None),
    ("role_policies_org_fk", "role_policies", ["org_id"], "orgs", ["id"], None),
    ("role_policies_changed_by_fk", "role_policies", ["changed_by"], "users", ["id"], None),
    ("invitations_org_fk", "invitations", ["org_id"], "orgs", ["id"], None),
    ("invitations_invited_by_fk", "invitations", ["invited_by"], "users", ["id"], None),
    (
        "mailboxes_deployment_fk",
        "mailboxes",
        ["org_id", "deployment_id"],
        "deployments",
        ["org_id", "id"],
        None,
    ),
    ("tickets_deployment_fk", "tickets", ["org_id", "deployment_id"], "deployments", ["org_id", "id"], None),
    (
        "tickets_deployment_version_fk",
        "tickets",
        ["org_id", "deployment_version_id"],
        "deployment_versions",
        ["org_id", "id"],
        None,
    ),
)


def upgrade() -> None:
    # ── Deployment versions: rollout states, edit tracking, config hash ──────────────────────────────
    op.drop_constraint("deployment_versions_state_ck", "deployment_versions", type_="check")
    op.create_check_constraint(
        "deployment_versions_state_ck",
        "deployment_versions",
        "state in ('draft', 'shadow', 'canary', 'published', 'retired')",
    )
    op.add_column("deployment_versions", sa.Column("canary_percent", sa.Integer(), nullable=True))
    op.add_column(
        "deployment_versions",
        sa.Column("config_hash", sa.Text(), nullable=False, server_default=sa.text("''::text")),
    )
    op.add_column("deployment_versions", sa.Column("edited_by", sa.Uuid(as_uuid=False), nullable=True))
    op.add_column("deployment_versions", sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("deployment_versions", sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "deployment_versions_canary_ck",
        "deployment_versions",
        "(state = 'canary') = (canary_percent is not null) "
        "and (canary_percent is null or canary_percent between 1 and 99)",
    )
    # At most one draft, one shadow, one canary and one published version per deployment at a time.
    op.create_index(
        "deployment_versions_live_uq",
        "deployment_versions",
        ["org_id", "deployment_id", "state"],
        unique=True,
        postgresql_where=sa.text("state <> 'retired'"),
    )
    op.create_check_constraint("deployments_status_ck", "deployments", "status in ('active', 'archived')")

    # ── Evals: splits, archive instead of delete, run integrity ──────────────────────────────────────
    op.add_column(
        "eval_cases", sa.Column("split", sa.Text(), nullable=False, server_default=sa.text("'test'::text"))
    )
    op.add_column("eval_cases", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("eval_cases_split_ck", "eval_cases", "split in ('calibration', 'test')")
    op.add_column(
        "eval_runs", sa.Column("config_hash", sa.Text(), nullable=False, server_default=sa.text("''::text"))
    )
    op.add_column(
        "eval_runs",
        sa.Column("dataset_snapshot", sa.Text(), nullable=False, server_default=sa.text("''::text")),
    )
    op.add_column(
        "eval_runs",
        sa.Column("split", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("eval_runs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "eval_runs_state_ck", "eval_runs", "state in ('queued', 'running', 'passed', 'failed', 'error')"
    )
    op.add_column(
        "eval_results", sa.Column("split", sa.Text(), nullable=False, server_default=sa.text("'test'::text"))
    )

    # ── Sender trust: the DKIM/SPF/DMARC verdict recorded at intake ─────────────────────────────────────
    op.add_column("inbound_messages", sa.Column("sender_auth", postgresql.JSONB(), nullable=True))
    op.add_column(
        "tickets",
        sa.Column("sender_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    # ── Composite tenant keys ────────────────────────────────────────────────────────────────────────
    for table, name in ORG_KEYS:
        op.create_unique_constraint(name, table, ["org_id", "id"])
    for name, table, cols, ref, ref_cols, ondelete in FKS:
        op.create_foreign_key(name, table, ref, cols, ref_cols, ondelete=ondelete)

    # ── Never zero admins ───────────────────────────────────────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION ci_memberships_keep_admin() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          -- A tenant that still has members must keep at least one admin. (Removing every member at once,
          -- e.g. a tenant reset, is allowed.)
          IF EXISTS (SELECT 1 FROM memberships WHERE org_id = OLD.org_id)
             AND NOT EXISTS (SELECT 1 FROM memberships WHERE org_id = OLD.org_id AND role = 'admin') THEN
            RAISE EXCEPTION 'a workspace must keep at least one admin' USING ERRCODE = 'check_violation',
              CONSTRAINT = 'memberships_last_admin';
          END IF;
          RETURN NULL;
        END $$""")
    op.execute("""
        CREATE CONSTRAINT TRIGGER memberships_keep_admin
          AFTER UPDATE OF role OR DELETE ON memberships
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW WHEN (OLD.role = 'admin')
          EXECUTE FUNCTION ci_memberships_keep_admin()""")

    _backfill_default_deployments()


def _backfill_default_deployments() -> None:
    from command_inbox.modules.deployments.defaults import (
        DEFAULT_KEY,
        Setup,
        build_default_config,
        config_json,
    )

    bind = op.get_bind()
    orgs = (
        bind.execute(sa.text("select id, name, confidence_bar from orgs order by created_at"))
        .mappings()
        .all()
    )
    for org in orgs:
        org_id = str(org["id"])
        exists = bind.execute(
            sa.text("select 1 from deployments where org_id = :o and key = :k"),
            {"o": org_id, "k": DEFAULT_KEY},
        ).first()
        if exists:
            continue

        def rows(sql: str, org_id: str = org_id) -> list[dict]:
            return [dict(r) for r in bind.execute(sa.text(sql), {"o": org_id}).mappings().all()]

        setup = Setup(
            org_name=org["name"],
            confidence_bar=float(org["confidence_bar"]),
            departments={
                str(r["id"]): r["name"]
                for r in rows("select id, name from departments where org_id = :o order by sort")
            },
            query_types=rows(
                "select name, default_lane, department_id, owner_label from query_types where org_id = :o "
                "order by sort, name"
            ),
            bucket_rules=rows(
                "select description, kind, pattern from bucket_rules where org_id = :o order by sort"
            ),
            priority_rules=rows(
                "select key, description, target, hard, enabled from priority_rules where org_id = :o order by sort"
            ),
        )
        config, notes = build_default_config(setup)
        note = "Created from the workspace's setup when deployments were introduced."
        if notes:
            note += " " + " ".join(notes)
        dep_id = bind.execute(
            sa.text(
                "insert into deployments (org_id, key, name, description) values (:o, :k, :n, :d) returning id"
            ),
            {
                "o": org_id,
                "k": DEFAULT_KEY,
                "n": "Default",
                "d": f"Every mailbox of {org['name']}, with the categories and rules it had before deployments.",
            },
        ).scalar_one()
        ver_id = bind.execute(
            sa.text(
                """insert into deployment_versions
                     (org_id, deployment_id, version, state, config, config_hash, notes, published_at)
                   values (:o, :d, 1, 'published', cast(:c as jsonb), :h, :n, now()) returning id"""
            ),
            {
                "o": org_id,
                "d": dep_id,
                "c": json.dumps(config_json(config)),
                "h": config.config_hash(),
                "n": note[:2000],
            },
        ).scalar_one()
        params = {"o": org_id, "d": dep_id, "v": ver_id}
        bind.execute(sa.text("update deployments set active_version_id = :v where id = :d"), params)
        bind.execute(
            sa.text("update mailboxes set deployment_id = :d where org_id = :o and deployment_id is null"),
            params,
        )
        bind.execute(
            sa.text(
                """update tickets set deployment_id = :d, deployment_version_id = :v
                    where org_id = :o and deployment_id is null"""
            ),
            params,
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS memberships_keep_admin ON memberships")
    op.execute("DROP FUNCTION IF EXISTS ci_memberships_keep_admin()")
    for name, table, *_ in reversed(FKS):
        op.drop_constraint(name, table, type_="foreignkey")
    for table, name in reversed(ORG_KEYS):
        op.drop_constraint(name, table, type_="unique")
    op.drop_column("tickets", "sender_verified")
    op.drop_column("inbound_messages", "sender_auth")
    op.drop_column("eval_results", "split")
    op.drop_constraint("eval_runs_state_ck", "eval_runs", type_="check")
    for col in ("started_at", "split", "dataset_snapshot", "config_hash"):
        op.drop_column("eval_runs", col)
    op.drop_constraint("eval_cases_split_ck", "eval_cases", type_="check")
    op.drop_column("eval_cases", "archived_at")
    op.drop_column("eval_cases", "split")
    op.drop_constraint("deployments_status_ck", "deployments", type_="check")
    op.drop_index("deployment_versions_live_uq", table_name="deployment_versions")
    op.drop_constraint("deployment_versions_canary_ck", "deployment_versions", type_="check")
    op.execute("update deployment_versions set state = 'retired' where state in ('shadow', 'canary')")
    for col in ("retired_at", "edited_at", "edited_by", "config_hash", "canary_percent"):
        op.drop_column("deployment_versions", col)
    op.drop_constraint("deployment_versions_state_ck", "deployment_versions", type_="check")
    op.create_check_constraint(
        "deployment_versions_state_ck", "deployment_versions", "state in ('draft', 'published', 'retired')"
    )
    # The backfilled default deployments are kept: tickets reference them.
