"""One test per hard bar, each asserting the case the bar exists to stop.

The rules in ``PolicyEngine.decide`` can reach a contradictory set — R2 adds a
block while R7 forbids one, R3 closes a case that R6 has opened. The gates are
what make the result coherent, and each is tested alone here so a failure names
the bar rather than the engine.

Every gate must also be idempotent: the pipeline runs once, but a caller that
ran it twice must get the same list.
"""

from __future__ import annotations

from sentinel.domain.enums import Action, Route, Verdict
from sentinel.policy.engine import RecommendationSet
from sentinel.policy.gates import (
    CloseNoFraudExclusivityGate,
    R1NoWeakBlockGate,
    R7NeverBlockRecurringGate,
    R10BlockAllCardsGate,
    ReportNeedsCaseGate,
    RouteRecomputeGate,
    default_gates,
)
from sentinel.policy.routing import RoutingTable
from sentinel.policy.state import CaseState


def rec_set(state: CaseState, *actions: Action) -> RecommendationSet:
    recs = RecommendationSet(RoutingTable())
    for action in actions:
        recs.add(action, "seeded by the test", state)
    return recs


# ── R1: no block on a single weak signal ─────────────────────────────────────


def test_r1_strips_a_block_when_one_signal_sits_below_070():
    # "Blocking a legitimate customer on one signal is a policy breach."
    state = CaseState(fraud_probability=0.65, independent_signals=1, customer_response=None)
    recs = rec_set(state, Action.BLOCK_CARD, Action.CREATE_CASE)
    outcome = R1NoWeakBlockGate().apply(recs, state)

    assert Action.BLOCK_CARD not in recs.actions()
    assert Action.CREATE_CASE in recs.actions()
    assert outcome.fired
    assert Action.BLOCK_CARD in outcome.removed


def test_r1_also_strips_block_all_cards():
    state = CaseState(fraud_probability=0.5, independent_signals=1, customer_response=None)
    recs = rec_set(state, Action.BLOCK_ALL_CARDS)
    R1NoWeakBlockGate().apply(recs, state)
    assert recs.actions() == ()


def test_r1_allows_a_block_once_a_second_signal_exists():
    state = CaseState(fraud_probability=0.65, independent_signals=2, customer_response=None)
    recs = rec_set(state, Action.BLOCK_CARD)
    outcome = R1NoWeakBlockGate().apply(recs, state)
    assert Action.BLOCK_CARD in recs.actions()
    assert not outcome.fired


def test_r1_allows_a_block_once_the_probability_clears_070():
    state = CaseState(fraud_probability=0.70, independent_signals=1, customer_response=None)
    recs = rec_set(state, Action.BLOCK_CARD)
    R1NoWeakBlockGate().apply(recs, state)
    assert Action.BLOCK_CARD in recs.actions()


def test_r1_stands_down_once_the_customer_has_answered():
    # The bar is "verify BEFORE you block". A denial is the verification.
    state = CaseState(fraud_probability=0.4, independent_signals=1, customer_response="denied")
    recs = rec_set(state, Action.BLOCK_CARD)
    R1NoWeakBlockGate().apply(recs, state)
    assert Action.BLOCK_CARD in recs.actions()


# ── R7: never block a charge that matches the cardholder's own pattern ───────


def test_r7_never_blocks_a_recurring_charge_even_after_a_denial():
    # R7 is a bar, not a preference. A customer disputing their own subscription
    # is the single most likely way this system wrongs a real person.
    state = CaseState(
        fraud_probability=0.9,
        recurring_match=True,
        customer_response="denied",
        independent_signals=3,
    )
    recs = rec_set(state, Action.BLOCK_CARD, Action.CREATE_CASE, Action.WARN_CUSTOMER)
    outcome = R7NeverBlockRecurringGate().apply(recs, state)

    assert Action.BLOCK_CARD not in recs.actions()
    assert Action.CREATE_CASE in recs.actions()
    assert Action.WARN_CUSTOMER in recs.actions()
    assert outcome.fired


def test_r7_leaves_a_block_alone_when_nothing_recurs():
    state = CaseState(fraud_probability=0.9, recurring_match=False, independent_signals=3)
    recs = rec_set(state, Action.BLOCK_CARD)
    outcome = R7NeverBlockRecurringGate().apply(recs, state)
    assert Action.BLOCK_CARD in recs.actions()
    assert not outcome.fired


# ── R10: BLOCK_ALL_CARDS needs two compromised cards ────────────────────────


def test_r10_removes_block_all_cards_with_one_compromised_card():
    state = CaseState(
        fraud_probability=0.95,
        verdict=Verdict.FRAUD,
        independent_signals=3,
        cards_with_confirmed_fraud=1,
        credentials_compromised=False,
    )
    recs = rec_set(state, Action.BLOCK_ALL_CARDS, Action.BLOCK_CARD)
    outcome = R10BlockAllCardsGate().apply(recs, state)

    assert Action.BLOCK_ALL_CARDS not in recs.actions()
    assert Action.BLOCK_CARD in recs.actions(), "R10 bars the sweep, not the single block"
    assert outcome.fired


def test_r10_permits_block_all_cards_on_two_compromised_cards():
    state = CaseState(
        fraud_probability=0.95,
        verdict=Verdict.FRAUD,
        independent_signals=3,
        cards_with_confirmed_fraud=2,
    )
    recs = rec_set(state, Action.BLOCK_ALL_CARDS)
    outcome = R10BlockAllCardsGate().apply(recs, state)
    assert Action.BLOCK_ALL_CARDS in recs.actions()
    assert not outcome.fired


def test_r10_permits_block_all_cards_on_confirmed_credential_compromise():
    state = CaseState(
        fraud_probability=0.95,
        verdict=Verdict.FRAUD,
        independent_signals=3,
        cards_with_confirmed_fraud=0,
        credentials_compromised=True,
    )
    recs = rec_set(state, Action.BLOCK_ALL_CARDS)
    R10BlockAllCardsGate().apply(recs, state)
    assert Action.BLOCK_ALL_CARDS in recs.actions()


# ── coherence: a report always has a case behind it ─────────────────────────


def test_a_report_gets_a_case_inserted_before_it():
    state = CaseState(fraud_probability=0.9, verdict=Verdict.FRAUD, independent_signals=3)
    recs = rec_set(state, Action.FILE_REPORT)
    outcome = ReportNeedsCaseGate().apply(recs, state)

    assert recs.actions()[0] is Action.CREATE_CASE, "execution order: the case comes first"
    assert Action.FILE_REPORT in recs.actions()
    assert Action.CREATE_CASE in outcome.added


def test_a_report_with_a_case_already_present_is_left_alone():
    state = CaseState(fraud_probability=0.9, verdict=Verdict.FRAUD, independent_signals=3)
    recs = rec_set(state, Action.CREATE_CASE, Action.FILE_REPORT)
    outcome = ReportNeedsCaseGate().apply(recs, state)
    assert len(recs) == 2
    assert not outcome.fired


# ── coherence: closing cannot coexist with enforcement ──────────────────────


def test_close_no_fraud_is_dropped_beside_enforcement():
    # R3 adds CLOSE_NO_FRAUD on a confirmation; R6 may have added a report on the
    # same case. Shipping both would be incoherent to a reader.
    state = CaseState(fraud_probability=0.8, verdict=Verdict.FRAUD, independent_signals=2)
    for enforcement in (Action.BLOCK_CARD, Action.BLOCK_ALL_CARDS, Action.FILE_REPORT):
        recs = rec_set(state, Action.CLOSE_NO_FRAUD, enforcement)
        outcome = CloseNoFraudExclusivityGate().apply(recs, state)
        assert Action.CLOSE_NO_FRAUD not in recs.actions()
        assert enforcement in recs.actions()
        assert outcome.fired


def test_close_no_fraud_survives_without_enforcement():
    state = CaseState(fraud_probability=0.05, verdict=Verdict.LEGITIMATE, independent_signals=2)
    recs = rec_set(state, Action.CLOSE_NO_FRAUD, Action.MONITOR_CARD)
    outcome = CloseNoFraudExclusivityGate().apply(recs, state)
    assert Action.CLOSE_NO_FRAUD in recs.actions()
    assert not outcome.fired


# ── routes are recomputed, never trusted ────────────────────────────────────


def test_a_wrong_route_is_corrected_from_the_table():
    state = CaseState(fraud_probability=0.9, exposure_usd=2_600.0, independent_signals=3)
    recs = RecommendationSet(RoutingTable())
    recs.add(Action.BLOCK_CARD, "seeded", state)
    # Simulate a rule body that got it wrong by rebuilding at a lower exposure.
    cheap = CaseState(fraud_probability=0.9, exposure_usd=100.0, independent_signals=3)
    recs = RecommendationSet(RoutingTable())
    recs.add(Action.BLOCK_CARD, "seeded", cheap)
    assert recs.to_list()[0].route is Route.L1

    outcome = RouteRecomputeGate().apply(recs, state)  # exposure is really $2,600
    assert recs.to_list()[0].route is Route.L2
    assert outcome.fired


def test_the_2500_boundary_is_inclusive_of_l1():
    at = CaseState(fraud_probability=0.9, exposure_usd=2_500.00, independent_signals=3)
    over = CaseState(fraud_probability=0.9, exposure_usd=2_500.01, independent_signals=3)
    assert RoutingTable().route_for(Action.BLOCK_CARD, at.exposure_usd) is Route.L1
    assert RoutingTable().route_for(Action.BLOCK_CARD, over.exposure_usd) is Route.L2


# ── the pipeline as a whole ─────────────────────────────────────────────────


def test_the_published_pipeline_is_six_gates_in_a_fixed_order():
    names = [gate.name for gate in default_gates()]
    assert len(names) == 6
    assert names[-1] == RouteRecomputeGate.name, "routing must run last, over what gates inserted"
    assert names.index(ReportNeedsCaseGate.name) < names.index(RouteRecomputeGate.name)


def test_every_gate_is_idempotent():
    state = CaseState(
        fraud_probability=0.65,
        independent_signals=1,
        recurring_match=True,
        cards_with_confirmed_fraud=0,
        exposure_usd=3_000.0,
    )
    for gate in default_gates():
        recs = rec_set(
            state,
            Action.BLOCK_CARD,
            Action.BLOCK_ALL_CARDS,
            Action.FILE_REPORT,
            Action.CLOSE_NO_FRAUD,
        )
        gate.apply(recs, state)
        once = [r.as_dict() for r in recs]
        gate.apply(recs, state)
        assert [r.as_dict() for r in recs] == once, f"{gate.name} is not idempotent"
