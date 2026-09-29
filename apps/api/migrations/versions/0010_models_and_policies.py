"""Model policy, spend, SLA policies and labelled eval cases.

- `orgs.allowed_providers`: the System 2 providers this workspace's agreements allow (a deployment naming
  another is refused at save time, and a run falls back to people). `orgs.model_budget_monthly_minor`: the
  monthly model spend cap in the workspace currency (null: no cap).
- `model_spend`: spend and call count per workspace, calendar month (UTC) and provider.
- `sla_policies`: reply-time targets by priority and segment (null matches any), with an escalation rule;
  the most specific match wins, and built-in defaults apply when a workspace has none.
- `eval_cases.ticket_id`: the real mail a labelled case came from (masked), once per dataset.
- `eval_runs.provider`: a run that pins System 2 to one provider (provider comparisons); such a run never
  counts for publishing, which needs a run of the configuration exactly as it is.

Revision ID: 0010_models_and_policies
Revises: 0009_knowledge_retrieval
"""

from __future__ import annotations

from alembic import op

revision = "0010_models_and_policies"
down_revision = "0009_knowledge_retrieval"
branch_labels = None
depends_on = None


def _run(sql: str) -> None:
    for statement in sql.split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(
        """
        ALTER TABLE orgs
          ADD COLUMN allowed_providers text[] NOT NULL DEFAULT '{anthropic,openai}',
          ADD COLUMN model_budget_monthly_minor bigint,
          ADD CONSTRAINT orgs_model_budget_ck CHECK (model_budget_monthly_minor IS NULL OR model_budget_monthly_minor >= 0);
        CREATE TABLE model_spend (
          org_id uuid NOT NULL REFERENCES orgs(id),
          month date NOT NULL,
          provider text NOT NULL,
          spent_minor bigint NOT NULL DEFAULT 0,
          calls integer NOT NULL DEFAULT 0,
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT model_spend_pkey PRIMARY KEY (org_id, month, provider)
        );
        SELECT ci_enable_tenant_rls('model_spend');
        CREATE TABLE sla_policies (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          org_id uuid NOT NULL REFERENCES orgs(id),
          name text NOT NULL,
          priority text,
          segment text,
          escalation boolean NOT NULL DEFAULT false,
          minutes integer NOT NULL,
          sort integer NOT NULL DEFAULT 0,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT sla_policies_pkey PRIMARY KEY (id),
          CONSTRAINT sla_policies_priority_ck CHECK (priority IS NULL OR priority IN ('P1', 'P2', 'P3', 'P4')),
          CONSTRAINT sla_policies_minutes_ck CHECK (minutes BETWEEN 5 AND 43200),
          CONSTRAINT sla_policies_match_uq UNIQUE NULLS NOT DISTINCT (org_id, priority, segment, escalation)
        );
        SELECT ci_enable_tenant_rls('sla_policies');
        ALTER TABLE eval_runs ADD COLUMN provider text;
        ALTER TABLE eval_cases ADD COLUMN ticket_id uuid;
        CREATE UNIQUE INDEX eval_cases_ticket_uq ON eval_cases (org_id, dataset_id, ticket_id)
          WHERE ticket_id IS NOT NULL
        """
    )


def downgrade() -> None:
    _run(
        """
        DROP INDEX eval_cases_ticket_uq;
        ALTER TABLE eval_cases DROP COLUMN ticket_id;
        ALTER TABLE eval_runs DROP COLUMN provider;
        DROP TABLE sla_policies;
        DROP TABLE model_spend;
        ALTER TABLE orgs DROP CONSTRAINT orgs_model_budget_ck,
          DROP COLUMN model_budget_monthly_minor,
          DROP COLUMN allowed_providers
        """
    )
