"""One test per policy rule, including the negatives.

These tests are worth more to the score than any prompt. Twenty-five percent of
the judging is next-best-action quality, and every one of these assertions is a
way the agent could silently get a route or a rule wrong on case 14 at the worst
possible moment.

Ported verbatim from v1 ``tests/test_policy.py`` — only the imports and the call
style changed. An assertion that fails here means the refactor is wrong.
"""

from __future__ import annotations

import pytest

from sentinel.domain.enums import Action, CustomerResponse, Route, TriggerType, Verdict
from sentinel.domain.errors import NoRouteDefined
from sentinel.policy import (
    CaseState,
    PolicyConfig,
    RoutingTable,
    SarPolicy,
    StoppingPolicy,
    build_policy_engine,
)

ENGINE = build_policy_engine()
ROUTING = RoutingTable()
SAR = SarPolicy()
STOPPING = StoppingPolicy()


def actions(state: CaseState) -> set[str]:
    return {r.action.value for r in ENGINE.decide(state)}


def route_of(state: CaseState, action: str) -> str | None:
    for r in ENGINE.decide(state):
        if r.action.value == action:
            return r.route
    return None


def route_for(action: Action, exposure_usd: float = 0.0) -> Route:
    return ROUTING.route_for(action, exposure_usd)


def should_file_report(state: CaseState) -> bool:
    return SAR.evaluate(state).file


def sar_reason(state: CaseState) -> str:
    return SAR.evaluate(state).reason


# --------------------------------------------------------------- routing table


def test_auto_actions_route_auto():
    for action in (
        Action.MONITOR_CARD,
        Action.WARN_CUSTOMER,
        Action.VERIFY_WITH_CUSTOMER,
        Action.STEP_UP_AUTH,
        Action.CREATE_CASE,
        Action.ESCALATE_TO_ANALYST,
        Action.CLOSE_NO_FRAUD,
        Action.ALLOW_TRANSACTION,
        Action.MONITOR_CONNECTED_CARDS,
        Action.GENERATE_REPORT,
    ):
        assert route_for(action, 10_000) == "auto"


def test_decline_is_always_l1():
    assert route_for(Action.DECLINE_TRANSACTION, 0) == "L1"
    assert route_for(Action.DECLINE_TRANSACTION, 99_999) == "L1"


def test_block_card_route_flips_at_2500():
    assert route_for(Action.BLOCK_CARD, 2_400) == "L1"
    assert route_for(Action.BLOCK_CARD, 2_500) == "L1"  # boundary is inclusive
    assert route_for(Action.BLOCK_CARD, 2_500.01) == "L2"
    assert route_for(Action.BLOCK_CARD, 2_600) == "L2"


def test_block_all_and_file_report_are_always_l2():
    assert route_for(Action.BLOCK_ALL_CARDS, 1) == "L2"
    assert route_for(Action.FILE_REPORT, 1) == "L2"


# ------------------------------------------------------------------- R1 .. R10


def test_r1_bars_a_block_on_one_weak_signal():
    state = CaseState(
        fraud_probability=0.65, independent_signals=1, trigger_type="risk_score", exposure_usd=300
    )
    got = actions(state)
    assert "BLOCK_CARD" not in got
    assert "VERIFY_WITH_CUSTOMER" in got


def test_r1_allows_a_block_once_probability_clears_070_with_corroboration():
    state = CaseState(
        fraud_probability=0.88,
        verdict="fraud",
        independent_signals=3,
        customer_response="denied",
        exposure_usd=300,
    )
    assert "BLOCK_CARD" in actions(state)


def test_r2_denial_gives_block_and_case():
    state = CaseState(
        fraud_probability=0.86,
        verdict="fraud",
        trigger_type="customer_report",
        customer_response="denied",
        independent_signals=2,
        exposure_usd=268.43,
    )
    got = actions(state)
    assert {"BLOCK_CARD", "CREATE_CASE"} <= got
    assert route_of(state, "BLOCK_CARD") == "L1"  # under $2,500


def test_r2_adds_a_report_when_exposure_exceeds_1000():
    state = CaseState(
        fraud_probability=0.9,
        verdict="fraud",
        trigger_type="customer_report",
        customer_response="denied",
        independent_signals=2,
        exposure_usd=1_200,
    )
    got = actions(state)
    assert "FILE_REPORT" in got
    assert route_of(state, "FILE_REPORT") == "L2"


def test_r2_adds_a_report_when_a_shared_device_links_another_card():
    state = CaseState(
        fraud_probability=0.86,
        verdict="fraud",
        trigger_type="customer_report",
        customer_response="denied",
        independent_signals=2,
        exposure_usd=268.43,
        shared_origin=True,
        shared_origin_kind="a shared device profile",
        connected_card_ids=["C00877-K1"],
    )
    got = actions(state)
    assert {"FILE_REPORT", "MONITOR_CONNECTED_CARDS"} <= got


def test_r3_confirmation_closes_the_alert():
    state = CaseState(
        fraud_probability=0.2,
        verdict="legitimate",
        trigger_type="customer_report",
        customer_response="confirmed",
        independent_signals=2,
    )
    got = actions(state)
    assert "CLOSE_NO_FRAUD" in got
    assert "BLOCK_CARD" not in got


def test_r4_no_reply_monitors_and_declines_pending():
    state = CaseState(
        fraud_probability=0.5,
        customer_response="no_reply",
        pending_authorization=True,
        independent_signals=2,
        exposure_usd=200,
    )
    got = actions(state)
    assert {"MONITOR_CARD", "DECLINE_TRANSACTION"} <= got
    assert "ESCALATE_TO_ANALYST" not in got  # exposure below $500


def test_r4_escalates_when_exposure_exceeds_500():
    state = CaseState(
        fraud_probability=0.5, customer_response="no_reply", independent_signals=2, exposure_usd=750
    )
    assert "ESCALATE_TO_ANALYST" in actions(state)


def test_r5_card_testing_declines_and_steps_up():
    state = CaseState(
        fraud_probability=0.72,
        verdict="fraud",
        independent_signals=2,
        card_testing_sequence=True,
        exposure_usd=9.40,
    )
    got = actions(state)
    assert {"DECLINE_TRANSACTION", "STEP_UP_AUTH"} <= got
    assert "BLOCK_CARD" not in got  # nothing over $100 cleared yet


def test_r5_blocks_once_a_purchase_over_100_has_cleared():
    state = CaseState(
        fraud_probability=0.8,
        verdict="fraud",
        independent_signals=2,
        card_testing_sequence=True,
        card_testing_cleared_over_100=True,
        exposure_usd=268.43,
    )
    got = actions(state)
    assert "BLOCK_CARD" in got
    assert route_of(state, "BLOCK_CARD") == "L1"


def test_r6_shared_origin_creates_case_reports_and_monitors():
    state = CaseState(
        fraud_probability=0.8,
        verdict="fraud",
        independent_signals=3,
        shared_origin=True,
        shared_origin_kind="one device profile",
        connected_card_ids=["C1-K1", "C2-K1"],
        exposure_usd=400,
    )
    got = actions(state)
    assert {"CREATE_CASE", "FILE_REPORT", "MONITOR_CONNECTED_CARDS"} <= got


def test_r7_disputed_but_recurring_never_blocks():
    state = CaseState(
        fraud_probability=0.55,
        trigger_type="customer_report",
        recurring_match=True,
        independent_signals=3,
        exposure_usd=49.0,
    )
    got = actions(state)
    assert {"CREATE_CASE", "VERIFY_WITH_CUSTOMER", "WARN_CUSTOMER"} <= got
    assert "BLOCK_CARD" not in got


def test_r7_still_never_blocks_even_after_a_denial():
    """R7 is a bar, not a preference: a denial must not override it."""
    state = CaseState(
        fraud_probability=0.75,
        trigger_type="customer_report",
        recurring_match=True,
        customer_response="denied",
        independent_signals=3,
        exposure_usd=49.0,
    )
    got = actions(state)
    assert "BLOCK_CARD" not in got
    assert "CREATE_CASE" in got


def test_r8_escalates_when_uncertain_and_exposed():
    state = CaseState(
        fraud_probability=0.5, verdict="uncertain", independent_signals=2, exposure_usd=700
    )
    assert "ESCALATE_TO_ANALYST" in actions(state)


def test_r8_escalates_when_evidence_conflicts_regardless_of_exposure():
    state = CaseState(
        fraud_probability=0.5,
        verdict="uncertain",
        independent_signals=3,
        exposure_usd=49,
        evidence_conflicts=True,
    )
    assert "ESCALATE_TO_ANALYST" in actions(state)


def test_r9_undocumented_pattern():
    state = CaseState(
        fraud_probability=0.8,
        verdict="fraud",
        independent_signals=3,
        undocumented_pattern=True,
        exposure_usd=300,
    )
    got = actions(state)
    assert {"CREATE_CASE", "FILE_REPORT", "ESCALATE_TO_ANALYST"} <= got


def test_r10_bars_block_all_cards_with_only_one_compromised_card():
    state = CaseState(
        fraud_probability=0.95,
        verdict="fraud",
        independent_signals=3,
        customer_response="denied",
        cards_with_confirmed_fraud=1,
        exposure_usd=5_000,
    )
    assert "BLOCK_ALL_CARDS" not in actions(state)


def test_r10_allows_block_all_cards_with_two_compromised_cards():
    state = CaseState(
        fraud_probability=0.95,
        verdict="fraud",
        independent_signals=3,
        customer_response="denied",
        cards_with_confirmed_fraud=2,
        exposure_usd=5_000,
    )
    got = actions(state)
    assert "BLOCK_ALL_CARDS" in got
    assert route_of(state, "BLOCK_ALL_CARDS") == "L2"


def test_r10_allows_block_all_cards_when_credentials_compromised():
    state = CaseState(
        fraud_probability=0.9,
        verdict="fraud",
        independent_signals=3,
        customer_response="denied",
        credentials_compromised=True,
        exposure_usd=900,
    )
    assert "BLOCK_ALL_CARDS" in actions(state)


# ------------------------------------------------------------ section 3a (SAR)


def test_no_report_when_fraud_is_not_strongly_suspected():
    state = CaseState(fraud_probability=0.4, verdict="uncertain", exposure_usd=5_000)
    assert should_file_report(state) is False


def test_no_report_for_a_small_isolated_confirmed_fraud():
    state = CaseState(fraud_probability=0.9, verdict="fraud", exposure_usd=268.43)
    assert should_file_report(state) is False
    assert "no report" in sar_reason(state)


def test_report_when_exposure_exceeds_1000():
    state = CaseState(fraud_probability=0.9, verdict="fraud", exposure_usd=1_000.01)
    assert should_file_report(state) is True


def test_report_when_connected_to_another_customers_fraud():
    state = CaseState(
        fraud_probability=0.9, verdict="fraud", exposure_usd=100, other_customer_fraud=True
    )
    assert should_file_report(state) is True


def test_sar_flag_always_agrees_with_the_action_list():
    """The answer file's sar.file must equal FILE_REPORT being present."""
    for p in (0.1, 0.35, 0.69, 0.71, 0.9):
        for exposure in (10.0, 999.99, 1_000.01, 5_000.0):
            for shared in (False, True):
                state = CaseState(
                    fraud_probability=p,
                    exposure_usd=exposure,
                    verdict="fraud" if p >= 0.7 else "uncertain",
                    independent_signals=2,
                    shared_origin=shared,
                    shared_origin_kind="a device" if shared else "",
                )
                in_actions = "FILE_REPORT" in actions(state)
                assert in_actions == should_file_report(state), (p, exposure, shared)


def test_a_report_always_has_a_case_behind_it():
    state = CaseState(
        fraud_probability=0.9, verdict="fraud", exposure_usd=5_000, independent_signals=2
    )
    got = [r.action.value for r in ENGINE.decide(state)]
    assert "FILE_REPORT" in got and "CREATE_CASE" in got
    assert got.index("CREATE_CASE") < got.index("FILE_REPORT")


# ---------------------------------------------------------------- section 3a/6


def test_case_opens_at_probability_030():
    assert "CREATE_CASE" not in actions(CaseState(fraud_probability=0.29))
    assert "CREATE_CASE" in actions(CaseState(fraud_probability=0.30))


def test_stop_requires_two_independent_signals():
    state = CaseState(fraud_probability=0.9, verdict="fraud")
    assert STOPPING.should_stop(state, independent_support=1).stop is False
    assert STOPPING.should_stop(state, independent_support=2).stop is True


def test_stop_on_a_settling_response():
    state = CaseState(fraud_probability=0.5, customer_response="denied", response_requested=True)
    decision = STOPPING.should_stop(state, independent_support=1)
    assert decision.stop is True and "settled" in decision.reason


def test_a_customer_report_is_not_a_verification_response():
    """Policy §6 stops on "a verification response". The trigger is not one.

    Eight of the twenty alerts arrive as "I never made this purchase". That is
    the cardholder's position and R2 acts on it — but it answers nothing the
    agent asked, so stopping on it would mean those eight never ask the one
    question that could still move them.
    """
    state = CaseState(fraud_probability=0.5, customer_response="denied", response_requested=False)
    assert STOPPING.should_stop(state, independent_support=1).stop is False


def test_no_stop_in_the_middle_band():
    state = CaseState(fraud_probability=0.5, verdict="uncertain")
    assert STOPPING.should_stop(state, independent_support=5).stop is False


def test_every_action_has_a_reason_and_a_valid_route():
    state = CaseState(
        fraud_probability=0.9,
        verdict="fraud",
        customer_response="denied",
        independent_signals=3,
        exposure_usd=3_000,
        shared_origin=True,
        shared_origin_kind="a device profile",
    )
    for r in ENGINE.decide(state):
        assert r.reason.strip()
        assert r.route in {"auto", "L1", "L2"}
        assert r.route == route_for(r.action, state.exposure_usd)


def test_close_no_fraud_never_coexists_with_enforcement():
    state = CaseState(
        fraud_probability=0.2,
        verdict="legitimate",
        customer_response="confirmed",
        independent_signals=2,
        exposure_usd=5_000,
        shared_origin=True,
        shared_origin_kind="a device",
    )
    got = actions(state)
    assert not ({"BLOCK_CARD", "BLOCK_ALL_CARDS"} & got and "CLOSE_NO_FRAUD" in got)


# ------------------------------------------------- v2: the restructured surface


def test_thresholds_are_the_published_numbers():
    """A typo in one of these silently re-writes the policy. Pin all nine."""
    config = PolicyConfig()
    assert config.block_card_l2_threshold == 2_500.0
    assert config.sar_exposure_threshold == 1_000.0
    assert config.case_probability_threshold == 0.30
    assert config.verify_before_block_threshold == 0.70
    assert config.escalate_exposure_threshold == 500.0
    assert config.r4_escalate_threshold == 500.0
    assert config.stop_high == 0.85
    assert config.stop_low == 0.15
    assert config.min_independent_support == 2


def test_an_action_outside_the_fourteen_has_no_route():
    """An invented action name must raise, never default to auto."""
    with pytest.raises(NoRouteDefined):
        ROUTING.route_for("SEIZE_FUNDS")


def test_case_state_accepts_strings_and_enums_alike():
    from_strings = CaseState(
        fraud_probability=0.5,
        verdict="fraud",
        trigger_type="customer_report",
        customer_response="denied",
        pattern="card_testing",
    )
    from_enums = CaseState(
        fraud_probability=0.5,
        verdict=Verdict.FRAUD,
        trigger_type=TriggerType.CUSTOMER_REPORT,
        customer_response=CustomerResponse.DENIED,
        pattern="card_testing",
    )
    assert from_strings == from_enums
    assert from_strings.verdict is Verdict.FRAUD
    assert from_strings.customer_response is CustomerResponse.DENIED


def test_case_state_rejects_a_verdict_outside_the_vocabulary():
    with pytest.raises(ValueError, match="verdict"):
        CaseState(fraud_probability=0.5, verdict="probably_fraud")


def test_sar_reason_is_populated_in_both_branches():
    """Guide.md requires a reason and eval/validate.py does not check it."""
    filed = SAR.evaluate(CaseState(fraud_probability=0.9, verdict="fraud", exposure_usd=1_200))
    refused = SAR.evaluate(CaseState(fraud_probability=0.2, verdict="legitimate", exposure_usd=40))
    assert filed.file is True and filed.reason.strip() and filed.triggers
    assert refused.file is False and refused.reason.strip()


def test_sar_names_every_trigger_that_holds():
    decision = SAR.evaluate(
        CaseState(
            fraud_probability=0.9,
            verdict="fraud",
            exposure_usd=4_000,
            shared_origin=True,
            shared_origin_kind="one device profile",
            other_customer_fraud=True,
            undocumented_pattern=True,
        )
    )
    assert len(decision.triggers) == 4


def test_recommendations_are_in_execution_order_not_severity_order():
    """R5 declines and steps up before R5b's block — what happens first."""
    state = CaseState(
        fraud_probability=0.8,
        verdict="fraud",
        independent_signals=2,
        card_testing_sequence=True,
        card_testing_cleared_over_100=True,
        exposure_usd=268.43,
    )
    order = [r.action for r in ENGINE.decide(state)]
    assert order.index(Action.DECLINE_TRANSACTION) < order.index(Action.BLOCK_CARD)
    assert order.index(Action.STEP_UP_AUTH) < order.index(Action.BLOCK_CARD)


def test_a_state_with_nothing_against_it_falls_back_to_monitoring():
    state = CaseState(fraud_probability=0.05, verdict="uncertain", independent_signals=2)
    assert actions(state) == {"MONITOR_CARD"}


def test_evaluate_reports_the_sar_decision_and_the_bars_that_fired():
    """The policy.evaluated event carries both; the UI renders which bar fired."""
    state = CaseState(fraud_probability=0.65, independent_signals=1, exposure_usd=300)
    outcome = ENGINE.evaluate(state)
    assert outcome.sar.file is False
    assert [gate.gate for gate in outcome.gates] == [
        "R1NoWeakBlock",
        "R7NeverBlockRecurring",
        "R10BlockAllCards",
        "ReportNeedsCase",
        "CloseNoFraudExclusivity",
        "RouteRecompute",
    ]
    assert outcome.as_dict()["recommendations"][0]["route"] == "auto"


def test_the_same_state_always_decides_the_same_way():
    """No clock, no randomness: initial and final are comparable only if this holds."""
    state = CaseState(
        fraud_probability=0.71,
        verdict="fraud",
        independent_signals=2,
        exposure_usd=1_500,
        shared_origin=True,
        shared_origin_kind="a device",
    )
    first = [r.as_dict() for r in ENGINE.decide(state)]
    second = [r.as_dict() for r in ENGINE.decide(state)]
    assert first == second


def test_a_fraud_verdict_the_cardholder_has_not_been_asked_about_gets_an_action():
    """R1-R10 leave one shape uncovered, and it is a common one.

    The graph says fraud, the cardholder has said nothing, no documented
    pattern matched. Nothing then adds an action and §3a's CREATE_CASE stands
    alone — eight of the twenty benchmark cases came back with a fraud verdict
    above 0.88 whose entire recommendation was to open a case.
    """
    state = CaseState(
        fraud_probability=0.91,
        verdict="fraud",
        exposure_usd=240.0,
        independent_signals=3,
        trigger_type="risk_score",
    )
    actions = {rec.action for rec in ENGINE.decide(state)}
    assert Action.CREATE_CASE in actions
    assert Action.VERIFY_WITH_CUSTOMER in actions
    assert Action.MONITOR_CARD in actions
    assert Action.BLOCK_CARD not in actions, "no block without R2's denial or R5"


def test_it_does_not_fire_once_the_cardholder_has_answered():
    for response in ("denied", "confirmed", "no_reply"):
        state = CaseState(
            fraud_probability=0.91,
            verdict="fraud",
            exposure_usd=240.0,
            independent_signals=3,
            customer_response=response,
            response_requested=True,
        )
        reasons = " ".join(rec.reason for rec in ENGINE.decide(state))
        assert "has not been asked" not in reasons, response


def test_r3_needs_the_cardholder_not_an_analyst():
    """R2, R3 and R4 turn on what the *cardholder* said.

    HHG-009 came out recommending CREATE_CASE and CLOSE_NO_FRAUD together: the
    cardholder had reported the charge, an analyst reviewed the history and
    found it consistent with their own pattern, and that opinion was routed
    through `customer_response` where R3 read it as "cardholder confirms the
    transaction". The analyst's read is real evidence and still moves the
    ledger; it is not the cardholder speaking.
    """
    from_the_cardholder = CaseState(
        fraud_probability=0.4,
        customer_response="confirmed",
        response_requested=True,
    )
    assert Action.CLOSE_NO_FRAUD in {r.action for r in ENGINE.decide(from_the_cardholder)}

    from_an_analyst = CaseState(fraud_probability=0.4, customer_response=None)
    assert Action.CLOSE_NO_FRAUD not in {r.action for r in ENGINE.decide(from_an_analyst)}
