"""The pilot at a bank: stage changes with four-eyes Risk sign-off, and the incident log.

- `pilot_stage_requests`: a request to move the workspace between onboarding → shadow → assisted → live, with
  the gate evidence captured when it was made and the second person's decision. One pending per workspace.
- `pilot_incidents`: what went wrong during the pilot (P1–P4), including hard-stop misses found by people.
- `orgs.pilot_settings`: targets, the pre-pilot baseline and the named Risk approvers.

Revision ID: 0014_pilot
Revises: 0013_scim
"""

from __future__ import annotations

from alembic import op

revision = "0014_pilot"
down_revision = "0013_scim"
branch_labels = None
depends_on = None


def _run(sql: str) -> None:
    for statement in sql.split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(
        """
        CREATE TABLE pilot_stage_requests (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          org_id uuid NOT NULL REFERENCES orgs(id),
          from_stage text NOT NULL,
          to_stage text NOT NULL,
          reason text NOT NULL,
          evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
          state text NOT NULL DEFAULT 'pending',
          requested_by uuid NOT NULL,
          requested_at timestamptz NOT NULL DEFAULT now(),
          decided_by uuid,
          decided_at timestamptz,
          decision_note text NOT NULL DEFAULT '',
          CONSTRAINT pilot_stage_requests_pkey PRIMARY KEY (id),
          CONSTRAINT pilot_stage_requests_state_ck
            CHECK (state IN ('pending', 'approved', 'rejected', 'withdrawn'))
        );
        CREATE UNIQUE INDEX pilot_stage_requests_pending_uq ON pilot_stage_requests (org_id)
          WHERE state = 'pending';
        SELECT ci_enable_tenant_rls('pilot_stage_requests');
        CREATE TABLE pilot_incidents (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          org_id uuid NOT NULL REFERENCES orgs(id),
          severity text NOT NULL,
          kind text NOT NULL,
          title text NOT NULL,
          detail text NOT NULL DEFAULT '',
          ticket_id uuid,
          opened_by uuid NOT NULL,
          opened_at timestamptz NOT NULL DEFAULT now(),
          resolved_by uuid,
          resolved_at timestamptz,
          resolution text NOT NULL DEFAULT '',
          CONSTRAINT pilot_incidents_pkey PRIMARY KEY (id),
          CONSTRAINT pilot_incidents_severity_ck CHECK (severity IN ('P1', 'P2', 'P3', 'P4')),
          CONSTRAINT pilot_incidents_kind_ck
            CHECK (kind IN ('hard_stop_miss', 'wrong_reply', 'data_exposure', 'outage', 'other'))
        );
        CREATE INDEX pilot_incidents_org_opened_idx ON pilot_incidents (org_id, opened_at DESC);
        SELECT ci_enable_tenant_rls('pilot_incidents');
        ALTER TABLE orgs ADD COLUMN pilot_settings jsonb NOT NULL DEFAULT '{}'::jsonb
        """
    )


def downgrade() -> None:
    _run(
        """
        ALTER TABLE orgs DROP COLUMN pilot_settings;
        DROP TABLE pilot_incidents;
        DROP TABLE pilot_stage_requests
        """
    )
