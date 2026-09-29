"""Pilot numbers, all read from records over a window: draft acceptance, AI vs people agreement in shadow,
hard-stop misses, reply times against the pre-pilot baseline, and incidents.

- **Acceptance**: drafts decided in the window (sent or discarded); accepted = sent with an edit distance at
  or below the light-edit threshold.
- **Lane agreement**: triaged mail whose final lane (a person's label, else the ticket's lane after any
  override) matches the lane the AI chose.
- **Category agreement**: mail a person labelled (the labelling queue) whose label matches the category the
  AI chose, as recorded on its `triage.completed` audit event.
- **Hard-stop misses**: labelled mail a person marked as a hard stop that the AI did not stop, plus incidents
  logged as `hard_stop_miss`.
- **On time**: of mail whose deadline has passed or which was resolved, the share resolved by the deadline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.db.engine import rows
from command_inbox.modules.insights.monitoring import edit_distance

# The AI's own record of each triage, and a person's label for it where one exists.
_COMPARED = """
with t as (
  select id, number, subject, original_lane, lane, lane_note
    from tickets
   where org_id = :org and received_at >= :since and received_at < :until
     and status <> 'triaging' and merged_into_id is null
), ai as (
  select distinct on (entity_id) entity_id as ticket_id, data
    from audit_events
   where org_id = :org and action = 'triage.completed' and entity = 'ticket' and at >= :since
   order by entity_id, seq desc
), lab as (
  select distinct on (ticket_id) ticket_id, expected
    from eval_cases
   where org_id = :org and source = 'labelled' and ticket_id is not null and archived_at is null
   order by ticket_id, created_at desc
)
select t.id::text as id, t.number, t.subject, t.original_lane as ai_lane,
       coalesce(lab.expected->>'lane', t.lane) as final_lane,
       ai.data->>'category' as ai_category,
       coalesce(ai.data->>'hardStop',
                case when t.original_lane = 'manual' and t.lane_note ilike 'held back%'
                     then substr(t.lane_note, 13) end) as ai_hard_stop,
       lab.expected->>'category' as human_category,
       coalesce((lab.expected->>'hardStop')::boolean, false) as human_hard_stop,
       lab.ticket_id is not null as labelled
  from t
  left join ai on ai.ticket_id = t.id::text
  left join lab on lab.ticket_id = t.id
 order by t.number desc
"""


@dataclass
class Compared:
    id: str
    number: int
    subject: str
    ai_lane: str
    final_lane: str
    ai_category: str | None
    ai_hard_stop: str | None
    human_category: str | None
    human_hard_stop: bool
    labelled: bool


@dataclass
class Kpis:
    since: datetime
    days: int
    drafts_decided: int = 0
    drafts_accepted: int = 0
    labelled: int = 0
    category_agreed: int = 0
    lane_compared: int = 0
    lane_agreed: int = 0
    hard_stop_misses: int = 0
    on_time_rate: float | None = None
    median_first_reply_minutes: float | None = None
    p1_incidents: int = 0
    open_incidents: int = 0
    compared: list[Compared] = field(default_factory=list)

    @property
    def acceptance_rate(self) -> float | None:
        return self.drafts_accepted / self.drafts_decided if self.drafts_decided else None

    @property
    def category_agreement(self) -> float | None:
        return self.category_agreed / self.labelled if self.labelled else None

    @property
    def lane_agreement(self) -> float | None:
        return self.lane_agreed / self.lane_compared if self.lane_compared else None


async def compared(tx: AsyncSession, org_id: str, since: datetime, until: datetime) -> list[Compared]:
    return [Compared(**r) for r in await rows(tx, _COMPARED, {"org": org_id, "since": since, "until": until})]


async def reply_times(
    tx: AsyncSession, org_id: str, since: datetime, until: datetime
) -> tuple[float | None, float | None]:
    """(on-time rate, median first reply in minutes) for mail received in the window."""
    [r] = await rows(
        tx,
        """select count(*) filter (where resolved_at is not null and resolved_at <= due_at) as on_time,
                  count(*) filter (where (resolved_at is not null) or due_at < :until) as judged,
                  percentile_cont(0.5) within group
                    (order by extract(epoch from first_reply_at - received_at) / 60)
                    filter (where first_reply_at is not null) as fr
             from tickets
            where org_id = :org and received_at >= :since and received_at < :until
              and merged_into_id is null and due_at is not null""",
        {"org": org_id, "since": since, "until": until},
    )
    rate = r["on_time"] / r["judged"] if r["judged"] else None
    return rate, (round(float(r["fr"]), 1) if r["fr"] is not None else None)


async def compute(
    tx: AsyncSession, org_id: str, since: datetime, until: datetime, light_edit_max: float
) -> Kpis:
    k = Kpis(since=since, days=max(0, (until - since).days))
    p = {"org": org_id, "since": since, "until": until}

    sent = await rows(
        tx,
        """select original_body, current_body from drafts
            where org_id = :org and state = 'sent' and sent_at >= :since and sent_at < :until
            order by sent_at desc limit 5000""",
        p,
    )
    [d] = await rows(
        tx,
        """select count(*) as n from drafts
            where org_id = :org and state = 'discarded' and updated_at >= :since and updated_at < :until""",
        p,
    )
    k.drafts_decided = len(sent) + int(d["n"])
    k.drafts_accepted = sum(
        1
        for r in sent
        if edit_distance((r["original_body"] or "").strip(), (r["current_body"] or "").strip())
        <= light_edit_max
    )

    k.compared = await compared(tx, org_id, since, until)
    k.lane_compared = len(k.compared)
    k.lane_agreed = sum(1 for c in k.compared if c.ai_lane == c.final_lane)
    labelled = [c for c in k.compared if c.labelled and c.human_category]
    k.labelled = len(labelled)
    k.category_agreed = sum(1 for c in labelled if c.ai_category == c.human_category)
    misses = sum(1 for c in k.compared if c.labelled and c.human_hard_stop and not c.ai_hard_stop)

    [inc] = await rows(
        tx,
        """select count(*) filter (where severity = 'P1' and opened_at >= :since) as p1,
                  count(*) filter (where resolved_at is null) as open,
                  count(*) filter (where kind = 'hard_stop_miss' and opened_at >= :since) as missed
             from pilot_incidents where org_id = :org""",
        p,
    )
    k.p1_incidents, k.open_incidents = int(inc["p1"]), int(inc["open"])
    k.hard_stop_misses = misses + int(inc["missed"])
    k.on_time_rate, k.median_first_reply_minutes = await reply_times(tx, org_id, since, until)
    return k
