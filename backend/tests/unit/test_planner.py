"""The planner orders the investigation; it does not choose it.

These tests exist because both alternatives were tried against the live model
and both were measured. Given a free choice, the planner dropped the five
exonerating detectors and returned p = 0.74 instead of 0.36 on identical facts.
Allowed to *add* to a mandatory core, it added a different set on each of four
identical runs and returned 0.3569, 0.3577 or 0.5025 — and every aggregate the
submission is judged on is computed from those numbers.
"""

from __future__ import annotations

from sentinel.agents.agents.planner import PlannedCall, PlannerAgent, PlanOutput
from sentinel.agents.budget import BudgetGuard, RunBudget
from sentinel.agents.context import InvestigationContext
from sentinel.config.settings import Settings
from sentinel.domain.alert import Alert
from sentinel.domain.enums import TriggerType
from sentinel.evidence.extractor import FeatureExtractor
from sentinel.evidence.ledger import EvidenceLedger
from sentinel.evidence.table import DEFAULT_ELT_PATH, EvidenceLikelihoodTable
from sentinel.graph.fake import FakeGraphRepository
from sentinel.tools.log import QueryLog
from sentinel.tools.registry import ToolRegistry

TABLE = EvidenceLikelihoodTable(DEFAULT_ELT_PATH)


def context(trigger: TriggerType = TriggerType.RISK_SCORE) -> InvestigationContext:
    settings = Settings(
        tg_host="https://example.invalid",
        tg_secret="s",
        openai_api_key="k",
        openai_model="m",
        openai_embedding_model="e",
    )
    alert = Alert(
        alert_id="HHG-000",
        opened_at="2016-12-10 15:01:21",
        trigger_type=trigger,
        trigger_text="t",
        flagged_txn_id="1",
        card_id="C1-K1",
        customer_id="C1",
    )
    return InvestigationContext(
        alert=alert,
        tools=ToolRegistry(FakeGraphRepository(), QueryLog()),
        ledger=EvidenceLedger(TABLE, trigger),
        extractor=FeatureExtractor(TABLE),
        guard=BudgetGuard(RunBudget(settings=settings)),
    )


def test_the_detector_set_is_the_same_whatever_the_planner_says():
    ctx = context()
    expected = set(PlannerAgent.static_plan(ctx))

    wanted_fewer = PlanOutput(tools=[PlannedCall(name="card_baseline", why="enough")])
    wanted_more = PlanOutput(
        tools=[
            PlannedCall(name="ring_expand", why="rings"),
            PlannedCall(name="device_neighbors", why="devices"),
            PlannedCall(name="email_cluster", why="emails"),
        ]
    )
    nonsense = PlanOutput(tools=[PlannedCall(name="not_a_tool", why="invented")])

    for output in (wanted_fewer, wanted_more, nonsense, PlanOutput()):
        assert set(PlannerAgent.order(output, ctx)) == expected


def test_the_planner_decides_the_order_which_is_what_the_ceiling_makes_matter():
    ctx = context()
    output = PlanOutput(
        tools=[
            PlannedCall(name="region_novelty", why="the score is about geography"),
            PlannedCall(name="amount_band_probe", why="then the amount"),
        ]
    )
    ordered = PlannerAgent.order(output, ctx)
    assert ordered[:2] == ["region_novelty", "amount_band_probe"]
    assert len(ordered) == len(set(ordered)), "no detector runs twice"


def test_every_trigger_asks_the_two_policy_bars():
    """R5 and R7 are bars, not preferences: they must run on every case."""
    for trigger in TriggerType:
        plan = PlannerAgent.static_plan(context(trigger))
        assert "recurring_charge_probe" in plan, f"R7's premise missing on {trigger.value}"
        assert "card_testing_probe" in plan, f"R5's premise missing on {trigger.value}"


def test_the_rationale_records_what_became_of_every_request():
    ctx = context()
    output = PlanOutput(
        tools=[
            PlannedCall(name="region_novelty", why="in plan"),
            PlannedCall(name="email_cluster", why="real tool, not in this plan"),
            PlannedCall(name="not_a_tool", why="invented"),
        ]
    )
    notes = {n["tool"]: n["status"] for n in PlannerAgent.rationale(output, ctx)}
    assert notes == {
        "region_novelty": "prioritised",
        "email_cluster": "not_in_plan",
        "not_a_tool": "unknown",
    }


# ── the pattern is a claim about the facts ───────────────────────────────────


def _ctx_with(txn, region=None, trigger=TriggerType.RISK_SCORE):
    ctx = context(trigger)
    ctx.txn = txn
    ctx.region_novelty = region
    return ctx


def test_out_of_region_is_rejected_where_the_card_has_history_there():
    """HHG-003: named `out_of_region_use` on a region with 42 prior charges.

    Guide.md defines it as "card-present purchases in a billing region the
    cardholder **has no history in**". The pattern is graded and it feeds the
    episode scoper, so a claim the measurements contradict cannot stand.
    """
    from sentinel.agents.steps import _coherent_pattern
    from sentinel.domain.enums import Pattern
    from sentinel.tools.dto import RegionNovelty, TransactionRow

    present = TransactionRow(txn_id="1", channel="in_person", addr1="330.0")
    established = RegionNovelty(prior_txns_in_region=42)
    novel = RegionNovelty(prior_txns_in_region=0)

    assert (
        _coherent_pattern(Pattern.OUT_OF_REGION_USE, _ctx_with(present, established))
        is Pattern.NONE
    )
    assert (
        _coherent_pattern(Pattern.OUT_OF_REGION_USE, _ctx_with(present, novel))
        is Pattern.OUT_OF_REGION_USE
    )


def test_card_not_present_is_rejected_on_a_card_present_transaction():
    from sentinel.agents.steps import _coherent_pattern
    from sentinel.domain.enums import Pattern
    from sentinel.tools.dto import TransactionRow

    present = TransactionRow(txn_id="1", channel="in_person")
    online = TransactionRow(txn_id="1", channel="online")
    for pattern in (Pattern.CARD_NOT_PRESENT_FRAUD, Pattern.CARD_NOT_PRESENT_NEW_DEVICE):
        assert _coherent_pattern(pattern, _ctx_with(present)) is Pattern.NONE
        assert _coherent_pattern(pattern, _ctx_with(online)) is pattern


def test_the_patterns_with_no_falsifiable_premise_are_left_alone():
    """Inventing a test for these would be checking the model's homework badly."""
    from sentinel.agents.steps import _coherent_pattern
    from sentinel.domain.enums import Pattern
    from sentinel.tools.dto import TransactionRow

    for pattern in (Pattern.ACCOUNT_TAKEOVER, Pattern.UNDOCUMENTED, Pattern.NONE):
        ctx = _ctx_with(TransactionRow(txn_id="1", channel="in_person"))
        assert _coherent_pattern(pattern, ctx) is pattern
