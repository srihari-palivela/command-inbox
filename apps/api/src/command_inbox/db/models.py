"""ORM models for the Command Inbox schema.

Generated from the migrated database with sqlacodegen, then renamed to singular class names.
Alembic owns the schema; change a table with a migration, then update the model here.
"""

import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ActionInstance(Base):
    __tablename__ = "action_instances"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="action_instances_pkey"),
        Index("action_instances_idem_uq", "org_id", "idempotency_key", unique=True),
        Index("action_instances_ticket_idx", "org_id", "ticket_id"),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    ticket_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    template_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    account_ref: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    fields: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    validation: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    state: Mapped[str] = mapped_column(Text, nullable=False)
    chain: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    maker_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    maker_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    checker_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    checker_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    execute_after: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    executed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    external_ref: Mapped[str | None] = mapped_column(Text)
    failure: Mapped[str | None] = mapped_column(Text)
    rejected_note: Mapped[str | None] = mapped_column(Text)


class ActionTemplate(Base):
    __tablename__ = "action_templates"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="action_templates_pkey"),
        Index("action_templates_code_uq", "org_id", "code", unique=True),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    system: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    owner: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    reversible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    money_moves: Mapped[bool] = mapped_column(Boolean, nullable=False)
    approval: Mapped[str] = mapped_column(Text, nullable=False)
    monthly_volume: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'::text"))
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    stp_pct: Mapped[int | None] = mapped_column(Integer)


class AgentBoard(Base):
    __tablename__ = "agent_boards"
    __table_args__ = (
        PrimaryKeyConstraint(
            "org_id", "agent_id", "board_id", name="agent_boards_org_id_agent_id_board_id_pk"
        ),
    )

    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    agent_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    board_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)


class AgentEval(Base):
    __tablename__ = "agent_evals"
    __table_args__ = (PrimaryKeyConstraint("id", name="agent_evals_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    agent_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    tone: Mapped[str] = mapped_column(Text, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (PrimaryKeyConstraint("id", name="agent_versions_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    agent_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    eval_status: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (PrimaryKeyConstraint("id", name="agents_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    abbr: Mapped[str] = mapped_column(Text, nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    cost_per_1k_minor: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    eval_score: Mapped[int | None] = mapped_column(Integer)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (PrimaryKeyConstraint("id", name="alerts_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    sev_label: Mapped[str] = mapped_column(Text, nullable=False)
    sev_kind: Mapped[str] = mapped_column(Text, nullable=False)
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    text_: Mapped[str] = mapped_column("text", Text, nullable=False)
    action_label: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    resolved_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (PrimaryKeyConstraint("id", name="approvals_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    ticket_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    subject_kind: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    step: Mapped[str] = mapped_column(Text, nullable=False)
    opened_evidence: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (PrimaryKeyConstraint("id", name="attachments_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    ticket_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    ext: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    storage_key: Mapped[str | None] = mapped_column(Text)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        PrimaryKeyConstraint("seq", name="audit_events_pkey"),
        Index("audit_org_seq_idx", "org_id", "seq"),
        Index("audit_ticket_idx", "org_id", "ticket_id"),
    )

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), nullable=False, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    actor_kind: Mapped[str] = mapped_column(Text, nullable=False)
    actor_name: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    prev_hash: Mapped[str] = mapped_column(Text, nullable=False)
    hash: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    entity_id: Mapped[str | None] = mapped_column(Text)
    ticket_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    feed_tone: Mapped[str | None] = mapped_column(Text)
    feed_meta: Mapped[str | None] = mapped_column(Text)


class AutonomyDial(Base):
    __tablename__ = "autonomy_dial"
    __table_args__ = (PrimaryKeyConstraint("org_id", "cell", name="autonomy_dial_org_id_cell_pk"),)

    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    cell: Mapped[str] = mapped_column(Text, primary_key=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    count_override: Mapped[int | None] = mapped_column(Integer)
    updated_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))


class Board(Base):
    __tablename__ = "boards"
    __table_args__ = (PrimaryKeyConstraint("id", name="boards_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    team: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    state: Mapped[str] = mapped_column(Text, nullable=False)
    auto_rate_pct: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    mailbox_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))


class Brief(Base):
    __tablename__ = "briefs"
    __table_args__ = (PrimaryKeyConstraint("ticket_id", name="briefs_pkey"),)

    ticket_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    why: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    suggestions: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))


class BucketRule(Base):
    __tablename__ = "bucket_rules"
    __table_args__ = (PrimaryKeyConstraint("id", name="bucket_rules_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    hits: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    pattern: Mapped[Any | None] = mapped_column(JSONB)


class Call(Base):
    __tablename__ = "calls"
    __table_args__ = (PrimaryKeyConstraint("id", name="calls_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    ticket_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    started_by: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    script: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    updates: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    live_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    ended_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    summary: Mapped[str | None] = mapped_column(Text)
    recording_key: Mapped[str | None] = mapped_column(Text)


class Clearance(Base):
    __tablename__ = "clearances"
    __table_args__ = (
        PrimaryKeyConstraint(
            "org_id", "user_id", "department_id", name="clearances_org_id_user_id_department_id_pk"
        ),
    )

    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    department_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="comments_pkey"),
        Index("comments_ticket_idx", "org_id", "ticket_id"),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    ticket_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    author_name: Mapped[str] = mapped_column(Text, nullable=False)
    author_initials: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    author_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))


class Connector(Base):
    __tablename__ = "connectors"
    __table_args__ = (PrimaryKeyConstraint("id", name="connectors_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    abbr: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class Counter(Base):
    __tablename__ = "counters"
    __table_args__ = (PrimaryKeyConstraint("org_id", "name", name="counters_org_id_name_pk"),)

    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)


class CourseCompletion(Base):
    __tablename__ = "course_completions"
    __table_args__ = (
        PrimaryKeyConstraint(
            "org_id", "course_id", "user_id", name="course_completions_org_id_course_id_user_id_pk"
        ),
    )

    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    course_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (PrimaryKeyConstraint("id", name="courses_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    cards: Mapped[Any] = mapped_column(JSONB, nullable=False)
    quiz: Mapped[Any] = mapped_column(JSONB, nullable=False)
    baseline_pct: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="customers_pkey"),
        Index("customers_email_idx", "org_id", "email"),
        Index("customers_org_cif_uq", "org_id", "cif", unique=True),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    cif: Mapped[str | None] = mapped_column(Text)  # None: an unmatched sender, not linked to a customer yet
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    segment: Mapped[str] = mapped_column(Text, nullable=False)
    since_year: Mapped[int | None] = mapped_column(Integer)
    account: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))


class DailyMetric(Base):
    __tablename__ = "daily_metrics"
    __table_args__ = (
        PrimaryKeyConstraint("org_id", "metric", "day", name="daily_metrics_org_id_metric_day_pk"),
    )

    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    metric: Mapped[str] = mapped_column(Text, primary_key=True)
    day: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    value: Mapped[float] = mapped_column(Double(53), nullable=False)


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (PrimaryKeyConstraint("id", name="departments_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    readiness_pct: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    readiness_note: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    gap_note: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    risk: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    in_matrix: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    owner_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))


class Draft(Base):
    __tablename__ = "drafts"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="drafts_pkey"),
        UniqueConstraint("ticket_id", name="drafts_ticket_id_unique"),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    ticket_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    to_addr: Mapped[str] = mapped_column(Text, nullable=False)
    original_body: Mapped[str] = mapped_column(Text, nullable=False)
    current_body: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    flagged: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    state: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    send_after: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    sent_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    sent_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (PrimaryKeyConstraint("id", name="feedback_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    text_: Mapped[str] = mapped_column("text", Text, nullable=False)
    fix: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'::text"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    agent_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    ticket_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    ticket_number: Mapped[str | None] = mapped_column(Text)
    diff: Mapped[Any | None] = mapped_column(JSONB)
    created_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))


class GapTicket(Base):
    __tablename__ = "gap_tickets"
    __table_args__ = (PrimaryKeyConstraint("id", name="gap_tickets_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    hits: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    cta: Mapped[str] = mapped_column(Text, nullable=False)
    opened_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    closed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        PrimaryKeyConstraint("org_id", "user_id", "key", name="idempotency_keys_org_id_user_id_key_pk"),
    )

    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    route: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    response: Mapped[Any | None] = mapped_column(JSONB)


class InboundMessage(Base):
    __tablename__ = "inbound_messages"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="inbound_messages_pkey"),
        Index("inbound_dedup_uq", "org_id", "mailbox_id", "provider_message_id", unique=True),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    mailbox_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    provider_message_id: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    ticket_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    # DKIM/SPF/DMARC verdict recorded at intake (migration 0004).
    sender_auth: Mapped[Any | None] = mapped_column(JSONB)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="jobs_pkey"),
        Index("jobs_dedupe_uq", "org_id", "dedupe_key", unique=True),
        Index("jobs_ready_idx", "state", "run_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    run_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'::text"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    locked_by: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    last_error: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(Text)


class KnowledgeDoc(Base):
    __tablename__ = "knowledge_docs"
    __table_args__ = (PrimaryKeyConstraint("id", name="knowledge_docs_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    section: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    owner: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    source_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    department_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    verified_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class KnowledgeSource(Base):
    __tablename__ = "knowledge_sources"
    __table_args__ = (PrimaryKeyConstraint("id", name="knowledge_sources_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    abbr: Mapped[str] = mapped_column(Text, nullable=False)
    doc_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    doc_unit: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'documents'::text"))
    approved_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    health: Mapped[str] = mapped_column(Text, nullable=False)
    sync_note: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    last_sync_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class Kpi(Base):
    __tablename__ = "kpis"
    __table_args__ = (PrimaryKeyConstraint("id", name="kpis_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    owner_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    metric: Mapped[str] = mapped_column(Text, nullable=False)
    viz: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[float] = mapped_column(Double(53), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class Mailbox(Base):
    __tablename__ = "mailboxes"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="mailboxes_pkey"),
        Index("mailboxes_org_address_uq", "org_id", "address", unique=True),
        Index("mailboxes_address_global_uq", text("lower(address)"), unique=True),
        ForeignKeyConstraint(
            ["org_id", "deployment_id"],
            ["deployments.org_id", "deployments.id"],
            name="mailboxes_deployment_fk",
        ),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    deployment_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    team_label: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    permissions: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    state: Mapped[str] = mapped_column(Text, nullable=False)
    volume_24h: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    department_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    credentials_enc: Mapped[str | None] = mapped_column(Text)
    last_sync_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="messages_pkey"),
        Index("messages_ticket_idx", "org_id", "ticket_id"),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    ticket_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    from_name: Mapped[str] = mapped_column(Text, nullable=False)
    from_addr: Mapped[str] = mapped_column(Text, nullable=False)
    to_addr: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(Text)


class NotificationRead(Base):
    __tablename__ = "notification_reads"
    __table_args__ = (
        PrimaryKeyConstraint(
            "org_id",
            "notification_id",
            "user_id",
            name="notification_reads_org_id_notification_id_user_id_pk",
        ),
    )

    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    notification_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    read_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (PrimaryKeyConstraint("id", name="notifications_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    urgent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    course_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    created_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))


class Org(Base):
    __tablename__ = "orgs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="orgs_pkey"),
        UniqueConstraint("slug", name="orgs_slug_unique"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="orgs_currency_iso4217"),
        CheckConstraint(
            "status in ('draft', 'provisioning', 'provisioned', 'onboarding', 'shadow', 'assisted', 'live', "
            "'suspended', 'archived')",
            name="orgs_status_ck",
        ),
        ForeignKeyConstraint(["created_by_operator"], ["platform_operators.id"]),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    short: Mapped[str] = mapped_column(Text, nullable=False)
    tint: Mapped[str] = mapped_column(Text, nullable=False)
    bg: Mapped[str] = mapped_column(Text, nullable=False)
    plan: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_bar: Mapped[float] = mapped_column(Double(53), nullable=False, server_default=text("0.78"))
    headcount: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )

    memberships: Mapped[list["Membership"]] = relationship("Membership", back_populates="org")
    sessions: Mapped[list["Session"]] = relationship("Session", back_populates="org")
    sso_idp_alias: Mapped[str | None] = mapped_column(Text)
    sso_email_domains: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # How this tenant's people read numbers, money and times: BCP 47 locale, ISO 4217, IANA zone.
    locale: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    time_zone: Mapped[str] = mapped_column(Text, nullable=False)
    # Lifecycle (platform console): draft → provisioning → provisioned → onboarding → shadow → assisted → live.
    status: Mapped[str] = mapped_column(Text, nullable=False)
    status_before_suspend: Mapped[str | None] = mapped_column(Text)
    status_changed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    legal_name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    region: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    data_residency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    support_email: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    limits: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_by_operator: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    # The admin's SSO connection: provider, directoryId, clientId, secretSealed, state, detail.
    sso_config: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class OutboxEvent(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="outbox_pkey"),
        Index("outbox_pending_idx", "dispatched_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    dispatched_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class PredictionOutcome(Base):
    __tablename__ = "prediction_outcomes"
    __table_args__ = (PrimaryKeyConstraint("id", name="prediction_outcomes_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Double(53), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    ticket_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    correct: Mapped[bool | None] = mapped_column(Boolean)


class PriorityRule(Base):
    __tablename__ = "priority_rules"
    __table_args__ = (PrimaryKeyConstraint("id", name="priority_rules_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    hard: Mapped[bool] = mapped_column(Boolean, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    hits: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))


class ProposedRule(Base):
    __tablename__ = "proposed_rules"
    __table_args__ = (PrimaryKeyConstraint("id", name="proposed_rules_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    text_: Mapped[str] = mapped_column("text", Text, nullable=False)
    proposed_by: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    proposed_by_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'::text"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    ticket_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    ticket_number: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))


class QueryType(Base):
    __tablename__ = "query_types"
    __table_args__ = (PrimaryKeyConstraint("id", name="query_types_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    default_lane: Mapped[str] = mapped_column(Text, nullable=False)
    monthly_volume: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    live: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    late_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    owner_label: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    show_on_map: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    show_on_speed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    map_name: Mapped[str | None] = mapped_column(Text)
    department_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    baseline_hours: Mapped[float | None] = mapped_column(Double(53))
    actual_hours: Mapped[float | None] = mapped_column(Double(53))


class Reply(Base):
    __tablename__ = "replies"
    __table_args__ = (PrimaryKeyConstraint("id", name="replies_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    ticket_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    send_after: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    author_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    sent_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class StaffAvailability(Base):
    __tablename__ = "staff_availability"
    __table_args__ = (PrimaryKeyConstraint("org_id", "user_id", name="staff_availability_org_id_user_id_pk"),)

    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    checkin: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    calendar: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class Subtask(Base):
    __tablename__ = "subtasks"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="subtasks_pkey"),
        Index("subtasks_ticket_key_uq", "org_id", "ticket_id", "key", unique=True),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    ticket_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    done_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    done_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class TicketLink(Base):
    __tablename__ = "ticket_links"
    __table_args__ = (PrimaryKeyConstraint("id", name="ticket_links_pkey"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    ticket_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    ref: Mapped[str | None] = mapped_column(Text)


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="tickets_pkey"),
        Index("tickets_assignee_idx", "org_id", "assignee_id"),
        Index("tickets_customer_idx", "org_id", "customer_id"),
        Index("tickets_due_idx", "org_id", "due_at"),
        Index("tickets_org_number_uq", "org_id", "number", unique=True),
        Index("tickets_status_idx", "org_id", "status"),
        Index("tickets_deployment_idx", "org_id", "deployment_id"),
        ForeignKeyConstraint(
            ["org_id", "deployment_id"],
            ["deployments.org_id", "deployments.id"],
            name="tickets_deployment_fk",
        ),
        ForeignKeyConstraint(
            ["org_id", "deployment_version_id"],
            ["deployment_versions.org_id", "deployment_versions.id"],
            name="tickets_deployment_version_fk",
        ),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    deployment_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    deployment_version_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    from_name: Mapped[str] = mapped_column(Text, nullable=False)
    from_email: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    lane: Mapped[str] = mapped_column(Text, nullable=False)
    original_lane: Mapped[str] = mapped_column(Text, nullable=False)
    lane_note: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False)
    segment: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'Retail'::text"))
    bucket: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    confidence: Mapped[float] = mapped_column(Double(53), nullable=False, server_default=text("0"))
    owner_kind: Mapped[str] = mapped_column(Text, nullable=False)
    sla_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    reopen_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    sentiment: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'neutral'::text"))
    next_move: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    category: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    subcategory: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    product: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    split_proposed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    logged_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    board_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    mailbox_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    customer_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    department_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    query_type_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    assignee_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    due_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    paused_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    first_reply_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    closed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    regulatory_flag: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    merged_into_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    accepted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    # An unauthenticated sender never reaches the Auto lane (migration 0004).
    sender_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class TraceSpan(Base):
    __tablename__ = "trace_spans"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="trace_spans_pkey"),
        Index("trace_spans_run_idx", "org_id", "run_id"),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    run_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    offset_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    agent: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    output: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    tokens: Mapped[int | None] = mapped_column(Integer)
    cost_minor: Mapped[int | None] = mapped_column(Integer)


class TriageRun(Base):
    __tablename__ = "triage_runs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="triage_runs_pkey"),
        Index("triage_runs_ticket_idx", "org_id", "ticket_id"),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    ticket_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    confidence: Mapped[float] = mapped_column(Double(53), nullable=False)
    lane: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_minor: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class UserSetting(Base):
    __tablename__ = "user_settings"
    __table_args__ = (PrimaryKeyConstraint("org_id", "user_id", name="user_settings_org_id_user_id_pk"),)

    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    prefs: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    signature: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="users_pkey"),
        UniqueConstraint("email", name="users_email_unique"),
        Index(
            "users_idp_subject_uq",
            "idp_subject",
            unique=True,
            postgresql_where=text("idp_subject is not null"),
        ),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    initials: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    idp_subject: Mapped[str | None] = mapped_column(Text)
    last_login_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))

    memberships: Mapped[list["Membership"]] = relationship("Membership", back_populates="user")
    sessions: Mapped[list["Session"]] = relationship("Session", back_populates="user")


class Watcher(Base):
    __tablename__ = "watchers"
    __table_args__ = (
        PrimaryKeyConstraint("org_id", "ticket_id", "user_id", name="watchers_org_id_ticket_id_user_id_pk"),
    )

    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint("role in ('staff', 'lead', 'admin')", name="memberships_role_ck"),
        ForeignKeyConstraint(["org_id"], ["orgs.id"], name="memberships_org_id_orgs_id_fk"),
        ForeignKeyConstraint(["user_id"], ["users.id"], name="memberships_user_id_users_id_fk"),
        PrimaryKeyConstraint("org_id", "user_id", name="memberships_org_id_user_id_pk"),
    )

    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    pod: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("12"))
    years: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    base_load: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    joined_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )

    org: Mapped["Org"] = relationship("Org", back_populates="memberships")
    user: Mapped["User"] = relationship("User", back_populates="memberships")


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        ForeignKeyConstraint(["org_id"], ["orgs.id"], name="sessions_org_id_orgs_id_fk"),
        ForeignKeyConstraint(["user_id"], ["users.id"], name="sessions_user_id_users_id_fk"),
        PrimaryKeyConstraint("id", name="sessions_pkey"),
        UniqueConstraint("token_hash", name="sessions_token_hash_unique"),
        Index("sessions_user_idx", "user_id"),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    csrf_token: Mapped[str] = mapped_column(Text, nullable=False)
    user_agent: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    device: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    location: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    ip: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    last_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))

    org: Mapped["Org"] = relationship("Org", back_populates="sessions")
    user: Mapped["User"] = relationship("User", back_populates="sessions")


# ── Added with the Python service (migration 0002) ────────────────────────────────────────────────────
# Tenancy, identity and access control


class RolePolicy(Base):
    """A tenant admin's override of a delegable capability for staff or team leads (see rbac/policy.py)."""

    __tablename__ = "role_policies"
    __table_args__ = (
        PrimaryKeyConstraint("org_id", "role", "capability", name="role_policies_pkey"),
        ForeignKeyConstraint(["org_id"], ["orgs.id"], name="role_policies_org_fk"),
        ForeignKeyConstraint(["changed_by"], ["users.id"], name="role_policies_changed_by_fk"),
    )

    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    role: Mapped[str] = mapped_column(Text, primary_key=True)
    capability: Mapped[str] = mapped_column(Text, primary_key=True)
    effect: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    changed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class Invitation(Base):
    """An admin invites someone by email with exactly one role; accepted at their first SSO sign-in."""

    __tablename__ = "invitations"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="invitations_pkey"),
        Index(
            "invitations_pending_uq",
            "org_id",
            "email",
            unique=True,
            postgresql_where=text("accepted_at is null and revoked_at is null"),
        ),
        CheckConstraint("email = lower(email)", name="invitations_email_lower_ck"),
        CheckConstraint(
            "invited_by is not null or invited_by_operator is not null", name="invitations_inviter_ck"
        ),
        Index(
            "invitations_token_uq", "token_hash", unique=True, postgresql_where=text("token_hash is not null")
        ),
        ForeignKeyConstraint(["org_id"], ["orgs.id"], name="invitations_org_fk"),
        ForeignKeyConstraint(["invited_by"], ["users.id"], name="invitations_invited_by_fk"),
        ForeignKeyConstraint(["invited_by_operator"], ["platform_operators.id"]),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    invited_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    accepted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    # SHA-256 of the single-use token in the emailed link; null for invitations accepted by SSO domain only.
    token_hash: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    invited_by_operator: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    sent_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    send_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


# Deployments: a tenant runs several mailbox categorisations side by side, each with its own taxonomy,
# rules and agentic flow. Configuration is versioned and immutable once published.


class Deployment(Base):
    __tablename__ = "deployments"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="deployments_pkey"),
        Index("deployments_org_key_uq", "org_id", "key", unique=True),
        UniqueConstraint("org_id", "id", name="deployments_org_id_id_uq"),
        ForeignKeyConstraint(["org_id"], ["orgs.id"], name="deployments_org_fk"),
        ForeignKeyConstraint(
            ["org_id", "active_version_id"],
            ["deployment_versions.org_id", "deployment_versions.id"],
            name="deployments_active_version_fk",
            use_alter=True,
        ),
        CheckConstraint("status in ('active', 'archived')", name="deployments_status_ck"),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'::text"))
    active_version_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    created_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class DeploymentVersion(Base):
    __tablename__ = "deployment_versions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="deployment_versions_pkey"),
        Index("deployment_versions_uq", "org_id", "deployment_id", "version", unique=True),
        # At most one draft, shadow, canary and published version per deployment at a time.
        Index(
            "deployment_versions_live_uq",
            "org_id",
            "deployment_id",
            "state",
            unique=True,
            postgresql_where=text("state <> 'retired'"),
        ),
        UniqueConstraint("org_id", "id", name="deployment_versions_org_id_id_uq"),
        ForeignKeyConstraint(
            ["org_id", "deployment_id"],
            ["deployments.org_id", "deployments.id"],
            name="deployment_versions_deployment_fk",
        ),
        ForeignKeyConstraint(
            ["org_id", "eval_run_id"],
            ["eval_runs.org_id", "eval_runs.id"],
            name="deployment_versions_eval_run_fk",
            use_alter=True,
        ),
        CheckConstraint(
            "state in ('draft', 'shadow', 'canary', 'published', 'retired')",
            name="deployment_versions_state_ck",
        ),
        CheckConstraint(
            "(state = 'canary') = (canary_percent is not null) "
            "and (canary_percent is null or canary_percent between 1 and 99)",
            name="deployment_versions_canary_ck",
        ),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    deployment_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'::text"))
    config: Mapped[Any] = mapped_column(JSONB, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    created_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    published_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    eval_run_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    canary_percent: Mapped[int | None] = mapped_column(Integer)
    # sha256 of the canonical config (DeploymentConfig.config_hash); publishing needs a passing run on it.
    config_hash: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    # Who last changed the config: that person cannot publish it (four-eyes).
    edited_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    edited_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    retired_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


# Evals: labelled cases per deployment, runs against a deployment version, per-case results.


class EvalDataset(Base):
    __tablename__ = "eval_datasets"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="eval_datasets_pkey"),
        UniqueConstraint("org_id", "id", name="eval_datasets_org_id_id_uq"),
        ForeignKeyConstraint(["org_id"], ["orgs.id"], name="eval_datasets_org_fk"),
        ForeignKeyConstraint(
            ["org_id", "deployment_id"],
            ["deployments.org_id", "deployments.id"],
            name="eval_datasets_deployment_fk",
        ),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    deployment_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class EvalCase(Base):
    __tablename__ = "eval_cases"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="eval_cases_pkey"),
        Index("eval_cases_dataset_idx", "org_id", "dataset_id"),
        UniqueConstraint("org_id", "id", name="eval_cases_org_id_id_uq"),
        ForeignKeyConstraint(
            ["org_id", "dataset_id"],
            ["eval_datasets.org_id", "eval_datasets.id"],
            name="eval_cases_dataset_fk",
            ondelete="CASCADE",
        ),
        CheckConstraint("split in ('calibration', 'test')", name="eval_cases_split_ck"),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    dataset_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    input: Mapped[Any] = mapped_column(JSONB, nullable=False)
    expected: Mapped[Any] = mapped_column(JSONB, nullable=False)
    tags: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'manual'::text"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    # Temperature and conformal threshold are fitted on `calibration`; gates are scored on `test` only.
    split: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'test'::text"))
    # Cases are archived, never deleted, so past runs keep their evidence.
    archived_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class EvalRun(Base):
    __tablename__ = "eval_runs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="eval_runs_pkey"),
        Index("eval_runs_version_idx", "org_id", "deployment_version_id"),
        UniqueConstraint("org_id", "id", name="eval_runs_org_id_id_uq"),
        ForeignKeyConstraint(
            ["org_id", "dataset_id"],
            ["eval_datasets.org_id", "eval_datasets.id"],
            name="eval_runs_dataset_fk",
        ),
        ForeignKeyConstraint(
            ["org_id", "deployment_version_id"],
            ["deployment_versions.org_id", "deployment_versions.id"],
            name="eval_runs_version_fk",
        ),
        CheckConstraint(
            "state in ('queued', 'running', 'passed', 'failed', 'error')", name="eval_runs_state_ck"
        ),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    dataset_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    deployment_version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'::text"))
    metrics: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    gates: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    engine: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    created_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    error: Mapped[str | None] = mapped_column(Text)
    # The run is bound to exactly what it scored: the version's config hash and the frozen dataset.
    config_hash: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    dataset_snapshot: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    split: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class EvalResult(Base):
    __tablename__ = "eval_results"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="eval_results_pkey"),
        Index("eval_results_run_idx", "org_id", "run_id"),
        ForeignKeyConstraint(
            ["org_id", "run_id"],
            ["eval_runs.org_id", "eval_runs.id"],
            name="eval_results_run_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "case_id"], ["eval_cases.org_id", "eval_cases.id"], name="eval_results_case_fk"
        ),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    run_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    case_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    output: Mapped[Any] = mapped_column(JSONB, nullable=False)
    scores: Mapped[Any] = mapped_column(JSONB, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    trace_id: Mapped[str | None] = mapped_column(Text)
    split: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'test'::text"))


# ── Platform (operators; cross-tenant, outside tenant RLS: tenants are referenced as tenant_id) ───────────


class PlatformOperator(Base):
    __tablename__ = "platform_operators"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="platform_operators_pkey"),
        UniqueConstraint("email", name="platform_operators_email_uq"),
        UniqueConstraint("idp_subject", name="platform_operators_subject_uq"),
        CheckConstraint("email = lower(email)", name="platform_operators_email_lower_ck"),
        CheckConstraint(
            "role in ('platform_owner', 'operator', 'support')", name="platform_operators_role_ck"
        ),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    idp_subject: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    last_login_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    disabled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class PlatformSession(Base):
    __tablename__ = "platform_sessions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="platform_sessions_pkey"),
        UniqueConstraint("token_hash", name="platform_sessions_token_uq"),
        ForeignKeyConstraint(["operator_id"], ["platform_operators.id"], ondelete="CASCADE"),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    operator_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    csrf_token: Mapped[str] = mapped_column(Text, nullable=False)
    ip: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    user_agent: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    last_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class PlatformAuditEvent(Base):
    __tablename__ = "platform_audit_events"
    __table_args__ = (
        PrimaryKeyConstraint("seq", name="platform_audit_events_pkey"),
        Index("platform_audit_tenant_idx", "tenant_id", "seq"),
    )

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    operator_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    operator_email: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    prev_hash: Mapped[str] = mapped_column(Text, nullable=False)
    hash: Mapped[str] = mapped_column(Text, nullable=False)


class TenantProvisioning(Base):
    __tablename__ = "tenant_provisioning"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "step", name="tenant_provisioning_pkey"),
        CheckConstraint(
            "state in ('pending', 'running', 'done', 'skipped', 'failed')",
            name="tenant_provisioning_state_ck",
        ),
        ForeignKeyConstraint(["tenant_id"], ["orgs.id"], ondelete="CASCADE"),
    )

    tenant_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    step: Mapped[str] = mapped_column(Text, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'::text"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    detail: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class TenantKey(Base):
    __tablename__ = "tenant_keys"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "version", name="tenant_keys_pkey"),
        CheckConstraint("state in ('active', 'retired', 'destroyed')", name="tenant_keys_state_ck"),
        CheckConstraint("(state = 'destroyed') = (wrapped_key is null)", name="tenant_keys_destroyed_ck"),
        Index(
            "tenant_keys_one_active_uq", "tenant_id", unique=True, postgresql_where=text("state = 'active'")
        ),
        ForeignKeyConstraint(["tenant_id"], ["orgs.id"], ondelete="CASCADE"),
    )

    tenant_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    kek_ref: Mapped[str] = mapped_column(Text, nullable=False)
    wrapped_key: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'::text"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    destroyed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class EmailMessage(Base):
    __tablename__ = "email_messages"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="email_messages_pkey"),
        CheckConstraint("state in ('queued', 'sent', 'logged', 'failed')", name="email_messages_state_ck"),
        Index("email_messages_tenant_idx", "tenant_id", "created_at"),
        ForeignKeyConstraint(["tenant_id"], ["orgs.id"], ondelete="SET NULL"),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    template: Mapped[str] = mapped_column(Text, nullable=False)
    to_addr: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body_sealed: Mapped[str | None] = mapped_column(Text)  # AES-GCM sealed; dropped once sent
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'::text"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    provider_message_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    sent_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
