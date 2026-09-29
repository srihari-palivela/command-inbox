"""The ⌘K copilot. Facts always come from the live queue; a System 2 model (when configured) only phrases
them. Suggested actions (open a filtered list, go to a screen) are decided here, never by the model."""

from __future__ import annotations

from collections import Counter

from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.context import Ctx
from command_inbox.domain.sla import at_risk
from command_inbox.domain.transitions import is_open
from command_inbox.modules.copilot.system2 import phrase_answer
from command_inbox.modules.tickets.queries import DEFAULT_BAR, list_tickets
from command_inbox.schemas import dto
from command_inbox.schemas.requests import TicketFilters

Action = dto.CopilotAnswerDTOActions


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _top_team(xs: list[dto.TicketSummaryDTO]) -> tuple[str, int] | None:
    counts = Counter(t.department for t in xs)
    # Ties keep first-seen order, like the JS Map + stable sort it replaces.
    return max(counts.items(), key=lambda kv: kv[1]) if counts else None


def _go(label: str, **filters: str) -> Action:
    return Action(label=label, filters=TicketFilters(**filters))


async def heuristic_answer(tx: AsyncSession, ctx: Ctx, question: str) -> dto.CopilotAnswerDTO:
    q = question.lower()

    def has(*words: str) -> bool:
        return any(w in q for w in words)

    all_ = (await list_tickets(tx, ctx, None)).items
    open_ = [t for t in all_ if is_open(t.status)]
    late = [t for t in open_ if at_risk(t.sla.tone)]
    approvals = [t for t in open_ if t.status == "awaiting_approval"]
    dual = [t for t in approvals if t.pending_gate in ("maker", "checker")]
    bar = DEFAULT_BAR
    low = [t for t in open_ if t.confidence < bar]

    headline: str
    lines: list[str]
    actions: list[Action]
    if has("deadline", "late", "overdue", "breach", "at risk", "miss"):
        worst = min(late, key=lambda t: t.sla.minutes_left or 0) if late else None
        team = _top_team(late)
        headline = f"{len(late)} open ticket{_plural(len(late), ' is', 's are')} inside the warning band."
        if worst:
            who = (
                worst.assignee.name
                if worst.assignee
                else ("the AI" if worst.owner_kind == "ai" else "nobody")
            )
            lines = [
                f"The tightest is {worst.number} — {worst.subject}",
                f"It sits with {who} and has {worst.sla.minutes_left or 0} minutes left.",
            ]
            if team:
                lines.append(
                    f"{team[0]} accounts for most of the risk ({team[1]} ticket{_plural(team[1], '', 's')})."
                )
        else:
            lines = ["Nothing is close to its deadline right now."]
        actions = [_go(f"Show the {len(late)} at-risk tickets", due="risk")]
    elif has("approval", "approve", "sign off", "waiting on me"):
        oldest = ", ".join(t.number for t in approvals[:3]) + (" are the oldest." if approvals else "")
        headline = (
            f"{len(approvals)} ticket{_plural(len(approvals), ' is', 's are')} waiting for a human decision."
        )
        lines = [
            line
            for line in (
                oldest,
                f"{len(dual)} carry a filled action; the rest are drafts waiting to be sent.",
            )
            if line.strip() and line != " are the oldest."
        ]
        actions = [_go("Show what needs approving", status="approval")]
    elif has("confidence", "unsure", "below the bar", "trade finance"):
        team = _top_team(low)
        headline = f"{len(low)} open ticket{_plural(len(low), ' falls', 's fall')} below the {bar:.2f} bar."
        lines = [
            f"{team[0]} is the weak spot ({team[1]} below the bar)." if team else "No team stands out.",
            "Low confidence tracks stale or missing approved content — see What it knows for the open gap tickets.",
        ]
        actions = [_go("Show the low-confidence tickets", conf="low")]
    elif has("how is the ai", "this week", "results", "performance", "saving", "doing"):
        if ctx.can("insights.view"):
            from command_inbox.modules.insights.service import results

            r = await results(tx, ctx)
            auto = next((c.pct for c in r.coverage if c.lane == "auto"), 0)
            draft = next((c.pct for c in r.coverage if c.lane == "draft"), 0)
            headline = (
                f"Time to resolve is down {_js(abs(r.delta_pct))}%, "
                f"from {_js(r.baseline_hours)}h to {_js(r.now_hours)}h."
            )
            lines = [
                f"The AI carries {_js(auto)}% of volume end to end and drafts another {_js(draft)}%.",
                f"That is {_js(r.capacity_multiple)}× the queries per person against the baseline.",
            ]
            actions = [Action(label="Open Results", to="/results")]
        else:
            headline = "Results are visible to team leads and admins."
            lines = ["Ask your team lead for this week’s numbers."]
            actions = []
    elif has("unowned", "unassigned", "no owner"):
        un = [t for t in open_ if t.owner_kind == "unassigned"]
        headline = f"{len(un)} open ticket{_plural(len(un), ' has', 's have')} no owner."
        lines = [
            "Nothing unowned can be automated — those queries route to a person every time.",
            *(f"{t.number} · {t.bucket}" for t in un[:2]),
        ]
        actions = [_go("Show unowned tickets", owner="unassigned")]
        if ctx.can("setup.view"):
            actions.append(Action(label="Open Who owns what", to="/setup/ownership"))
    else:
        ai_owned = sum(1 for t in open_ if t.owner_kind == "ai")
        headline = f"{len(open_)} ticket{_plural(len(open_), ' is', 's are')} open right now."
        lines = [
            f"{len(approvals)} wait on a human decision, {len(late)} are close to a deadline.",
            f"The AI is handling {ai_owned} on its own.",
            "Ask about deadlines, approvals, confidence or results for a sharper answer.",
        ]
        actions = [_go("Show all open tickets", due="open")]
    return dto.CopilotAnswerDTO(headline=headline, lines=lines, actions=actions, provider="heuristic")


def _js(x: float) -> str:
    """Numbers as JavaScript interpolates them (2.0 -> "2")."""
    return str(int(x)) if float(x).is_integer() else str(x)


async def ask(tx: AsyncSession, ctx: Ctx, question: str) -> dto.CopilotAnswerDTO:
    base = await heuristic_answer(tx, ctx, question)
    phrased = await phrase_answer(question, "\n".join([base.headline, *base.lines]))
    if phrased is not None:
        headline, lines = phrased
        return base.model_copy(update={"headline": headline, "lines": lines, "provider": "claude"})
    return base
