"""Domain rules: the same cases the previous service was tested with, plus the fixes found since."""

from datetime import UTC, datetime, timedelta

from command_inbox.domain.lane import LaneInputs, decide_lane
from command_inbox.domain.nl_filter import parse_natural_filters
from command_inbox.domain.pii import mask_pii, unmask
from command_inbox.domain.priority import PriorityRuleRow, PrioritySignals, rank_priority
from command_inbox.domain.risk import LOCKED_CELL, MAX_DIAL, cell_of, chain_for
from command_inbox.domain.sla import SlaInput, at_risk, compute_sla, sla_budget
from command_inbox.domain.transitions import allowed_transitions, can_transition, is_open


def test_cells_by_money_and_reversibility():
    assert [cell_of(True, False), cell_of(False, False), cell_of(True, True), cell_of(False, True)] == [
        "0-0",
        "0-1",
        "1-0",
        "1-1",
    ]


def test_only_reversible_no_money_cell_can_auto_execute():
    assert MAX_DIAL["0-0"] == 2
    assert all(MAX_DIAL[c] < 2 for c in ("0-1", "1-0", "1-1"))
    assert MAX_DIAL[LOCKED_CELL] == 0


def test_irreversible_is_always_dual():
    for mode in ("auto", "single", "dual"):
        assert chain_for("0-1", mode, 2) == "dual"
        assert chain_for("1-1", mode, 2) == "dual"


def test_template_can_raise_but_not_lower_the_bar():
    assert chain_for("1-0", "auto", 2) == "single_undo"
    assert chain_for("1-0", "dual", 0) == "dual"
    assert chain_for("0-0", "dual", 2) == "dual"
    assert chain_for("0-0", "auto", 2) == "auto"
    assert chain_for("0-0", "auto", 1) == "single_undo"


BASE = LaneInputs(
    hard_stop=None,
    confidence=0.94,
    bar=0.78,
    query_type_owned=True,
    multi_intent=False,
    has_template=True,
    fields_complete=True,
    coverage="full",
    informational=False,
)


def _with(**kw):
    from dataclasses import replace

    return replace(BASE, **kw)


def test_lane_order():
    assert decide_lane(BASE).lane == "auto"
    assert decide_lane(_with(hard_stop="ombudsman named")).note == "Held back — ombudsman named"
    assert decide_lane(_with(multi_intent=True)).lane == "manual"
    assert decide_lane(_with(query_type_owned=False)).lane == "manual"
    assert decide_lane(_with(confidence=0.7)).lane == "manual"
    assert decide_lane(_with(fields_complete=False)).lane == "manual"


def test_unverified_sender_never_fills_an_action():
    assert decide_lane(_with(sender_verified=False)).lane == "manual"


def test_drafts_only_from_approved_content():
    info = _with(has_template=False, informational=True)
    assert decide_lane(info).lane == "draft"
    assert (
        "read it before sending"
        in decide_lane(_with(has_template=False, informational=True, confidence=0.6)).note
    )
    assert decide_lane(_with(has_template=False, informational=True, coverage="none")).lane == "manual"


CALM = PrioritySignals(
    regulator_named=False,
    vulnerable=False,
    minutes_left=1000,
    amount_inr=None,
    contact_count=1,
    informational=False,
)


def test_priority_rules():
    from dataclasses import replace

    assert rank_priority(CALM, [])[0] == "P3"
    assert rank_priority(replace(CALM, informational=True), [])[0] == "P4"
    assert rank_priority(replace(CALM, contact_count=3), [])[0] == "P2"
    assert rank_priority(replace(CALM, amount_inr=18_40_000), [])[0] == "P2"
    assert rank_priority(replace(CALM, informational=True, regulator_named=True), [])[0] == "P1"
    assert rank_priority(replace(CALM, contact_count=5), [PriorityRuleRow("p4", False, False)])[0] == "P3"
    assert rank_priority(replace(CALM, regulator_named=True), [PriorityRuleRow("p1", True, False)])[0] == "P1"


NOW = datetime(2026, 9, 27, 10, tzinfo=UTC)


def _due(mins: float) -> datetime:
    return NOW + timedelta(minutes=mins)


def test_sla_grading():
    assert compute_sla(SlaInput("with_human", _due(600), 1440, None), NOW).tone == "due_soon"
    assert compute_sla(SlaInput("with_human", _due(1000), 1440, None), NOW).tone == "on_track"
    assert compute_sla(SlaInput("with_human", _due(45), 1440, None), NOW).tone == "almost_late"
    late = compute_sla(SlaInput("with_human", _due(-5), 240, None), NOW)
    assert (late.tone, late.minutes_left) == ("late", -5)
    paused = compute_sla(SlaInput("waiting_customer", _due(300), 1440, NOW - timedelta(minutes=60)), NOW)
    assert (paused.tone, paused.minutes_left) == ("paused", 360)
    assert compute_sla(SlaInput("resolved", _due(-500), 240, None), NOW).tone == "closed"
    assert [t for t in ("on_track", "due_soon", "almost_late", "late", "paused", "closed") if at_risk(t)] == [
        "due_soon",
        "almost_late",
        "late",
    ]
    assert (
        sla_budget("P1", "Corporate"),
        sla_budget("P1", "Retail"),
        sla_budget("P3", "Retail"),
        sla_budget("P4", "Retail", True),
    ) == (240, 480, 1440, 480)


def test_transitions():
    assert allowed_transitions("executing") == []
    assert not can_transition("awaiting_approval", "resolved")
    assert can_transition("closed", "with_human")
    assert not is_open("closed") and is_open("waiting_customer")


def test_pii_masking_round_trip():
    raw = (
        "Mail m.raghavan@sundaramtextiles.in or call +91 9840014471; PAN ABCDE1234F, "
        "card 4111 1111 1111 1111, A/C 123456789012, IFSC HDFC0001234."
    )
    masked = mask_pii(raw)
    for secret in ("raghavan", "ABCDE1234F", "4111", "123456789012", "9840014471", "HDFC0001234"):
        assert secret not in masked.text
    assert "[CARD_1]" in masked.text and "[PAN_1]" in masked.text
    assert unmask(masked.text, masked.vault) == raw


def test_repeated_value_same_token():
    assert mask_pii("a@b.co wrote; reply to a@b.co").text == "[EMAIL_1] wrote; reply to [EMAIL_1]"


def test_non_luhn_16_digits_are_not_cards():
    assert "[CARD_" not in mask_pii("order 1234 5678 9012 3456").text


DEPTS = [{"id": "d1", "name": "Chargeback & Disputes"}, {"id": "d2", "name": "Trade & Payments"}]


def test_nl_filter():
    r = parse_natural_filters("late disputes assigned to me", DEPTS)
    assert r.filters == {"team": "d1", "owner": "mine", "due": "risk"}
    assert [c.text for c in r.chips] == ["Team: Chargeback & Disputes", "Owner: me", "Running late"]
    assert parse_natural_filters("P1 drafts below the bar waiting for approval", DEPTS).filters == {
        "status": "approval",
        "lane": "draft",
        "conf": "low",
        "pri": "P1",
    }
    assert parse_natural_filters("hello there", DEPTS).chips == []
