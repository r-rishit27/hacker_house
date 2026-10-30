"""Episode scoping, state building and the evidence simulator.

Three classes, one theme: everything the policy engine reads must be a
measurement, not a judgement. If any of these quietly guesses, the policy is
still deterministic and still wrong.
"""

from __future__ import annotations

import pytest

from sentinel.agents.episode import EpisodeScope, EpisodeScoper
from sentinel.agents.state_builder import CaseStateBuilder, ScopedFacts
from sentinel.domain.enums import CustomerResponse, Pattern, RequestType, TriggerType, Verdict
from sentinel.evidence.ledger import EvidenceLedger
from sentinel.evidence.table import DEFAULT_ELT_PATH, EvidenceLikelihoodTable
from sentinel.simulation.simulator import EvidenceSimulator
from sentinel.tools.dto import (
    CardTestingProbe,
    CardWindow,
    CustomerCaseHistory,
    DeviceNovelty,
    RecurringChargeProbe,
    RegionNovelty,
    RingExpansion,
    TransactionRow,
)

CARD = "C08623-K2"
TXN = "3530164"


@pytest.fixture(scope="module")
def table() -> EvidenceLikelihoodTable:
    return EvidenceLikelihoodTable(DEFAULT_ELT_PATH)


def row(txn_id: str, ts: str, amt: float, **kw) -> dict:
    base = {
        "txn_id": txn_id,
        "ts": ts,
        "amt": amt,
        "channel": "online",
        "risk_score": 0.2,
        "addr1": "330.0",
    }
    base.update(kw)
    return base


def window(*rows: dict) -> CardWindow:
    return CardWindow.model_validate({"window": list(rows)})


FLAGGED = TransactionRow.model_validate(
    row(TXN, "2016-12-10 13:01:21", 49.0, channel="in_person", risk_score=0.4)
)


# ═══ episode scoping ═════════════════════════════════════════════════════════


def test_a_legitimate_verdict_claims_no_episode_at_all():
    # The contract is explicit, and no pattern may override it.
    scoper = EpisodeScoper(window=window(row("1", "2016-12-10 12:00:00", 500.0, risk_score=0.95)))
    scope = scoper.scope(FLAGGED, Pattern.CARD_NOT_PRESENT_FRAUD, Verdict.LEGITIMATE)
    assert scope == EpisodeScope((), "", 0.0, scope.basis)
    assert scope.is_empty


def test_an_unnamed_pattern_claims_only_the_flagged_transaction():
    scoper = EpisodeScoper(window=window(row("other", "2016-12-10 12:00:00", 900.0)))
    scope = scoper.scope(FLAGGED, Pattern.NONE, Verdict.UNCERTAIN)
    assert scope.affected_txn_ids == (TXN,)
    assert scope.exposure_usd == 49.0


def test_card_testing_pulls_in_the_probes_and_what_cleared():
    scoper = EpisodeScoper(
        window=window(
            row("p1", "2016-12-10 12:40:00", 1.5),
            row("p2", "2016-12-10 12:45:00", 2.0),
            row("p3", "2016-12-10 12:50:00", 1.0),
            row("big", "2016-12-10 13:30:00", 268.43),
            row(TXN, "2016-12-10 13:01:21", 49.0),
        ),
        testing=CardTestingProbe(
            small_online_auths_1h=3,
            small_txn_ids=["p1", "p2", "p3"],
            largest_purchase_after=268.43,
            purchases_after_ids=["big"],
        ),
    )
    scope = scoper.scope(FLAGGED, Pattern.CARD_TESTING, Verdict.FRAUD)
    assert set(scope.affected_txn_ids) == {"p1", "p2", "p3", "big", TXN}
    assert scope.first_suspicious_txn_id == "p1", "earliest first"
    assert scope.exposure_usd == round(1.5 + 2.0 + 1.0 + 268.43 + 49.0, 2)


def test_a_cnp_episode_takes_only_transactions_that_incriminate_themselves():
    # Proximity in time is not evidence. A quiet $12 purchase an hour later is
    # not part of the episode just because it is nearby.
    scoper = EpisodeScoper(
        window=window(
            row(TXN, "2016-12-10 13:01:21", 49.0),
            row("hot", "2016-12-10 14:00:00", 300.0, risk_score=0.91),
            row("newdev", "2016-12-10 15:00:00", 120.0, device_new="New"),
            row("quiet", "2016-12-10 16:00:00", 12.0, risk_score=0.10),
            row("inperson", "2016-12-10 17:00:00", 80.0, risk_score=0.95, channel="in_person"),
        )
    )
    scope = scoper.scope(FLAGGED, Pattern.CARD_NOT_PRESENT_FRAUD, Verdict.FRAUD)
    assert set(scope.affected_txn_ids) == {TXN, "hot", "newdev"}
    assert "quiet" not in scope.affected_txn_ids
    assert "inperson" not in scope.affected_txn_ids, "a CNP episode is online only"


def test_a_cnp_episode_respects_the_48_hour_span():
    scoper = EpisodeScoper(
        window=window(
            row(TXN, "2016-12-10 13:01:21", 49.0),
            row("inside", "2016-12-11 13:00:00", 200.0, risk_score=0.9),
            row("outside", "2016-12-14 13:00:00", 200.0, risk_score=0.9),
        )
    )
    scope = scoper.scope(FLAGGED, Pattern.CARD_NOT_PRESENT_FRAUD, Verdict.FRAUD)
    assert "inside" in scope.affected_txn_ids
    assert "outside" not in scope.affected_txn_ids


def test_out_of_region_use_takes_the_region_from_its_first_appearance():
    scoper = EpisodeScoper(
        window=window(
            row(TXN, "2016-12-10 13:01:21", 49.0, addr1="444.0"),
            row("same_region", "2016-12-11 10:00:00", 75.0, addr1="444.0"),
            row("home", "2016-12-11 11:00:00", 60.0, addr1="330.0"),
            row("before", "2016-12-01 10:00:00", 20.0, addr1="444.0"),
        ),
        region=RegionNovelty(first_seen_in_region="2016-12-09 00:00:00"),
    )
    flagged = TransactionRow.model_validate(row(TXN, "2016-12-10 13:01:21", 49.0, addr1="444.0"))
    scope = scoper.scope(flagged, Pattern.OUT_OF_REGION_USE, Verdict.FRAUD, region_code="444.0")
    assert set(scope.affected_txn_ids) == {TXN, "same_region"}
    assert "home" not in scope.affected_txn_ids
    assert "before" not in scope.affected_txn_ids, "predates the region's first appearance"


def test_exposure_is_the_sum_of_absolute_amounts_rounded_to_cents():
    scoper = EpisodeScoper(
        window=window(
            row(TXN, "2016-12-10 13:01:21", 49.0),
            row("refund", "2016-12-10 14:00:00", -30.0, risk_score=0.95),
        )
    )
    scope = scoper.scope(FLAGGED, Pattern.CARD_NOT_PRESENT_FRAUD, Verdict.FRAUD)
    assert scope.exposure_usd == 79.0, "absolute amounts; a refund still exposes money"


def test_the_first_suspicious_transaction_is_always_in_the_episode():
    # The validator warns when it is not, and the brief says "where it started".
    scoper = EpisodeScoper(
        window=window(
            row(TXN, "2016-12-10 13:01:21", 49.0),
            row("earlier", "2016-12-10 09:00:00", 200.0, risk_score=0.93),
        )
    )
    scope = scoper.scope(FLAGGED, Pattern.CARD_NOT_PRESENT_FRAUD, Verdict.FRAUD)
    assert scope.first_suspicious_txn_id == "earlier"
    assert scope.first_suspicious_txn_id in scope.affected_txn_ids


# ═══ state building ══════════════════════════════════════════════════════════


def test_independent_signals_comes_from_the_ledger_not_a_default(table):
    """The seam v1 never closed.

    Left at its default of 1, R1 strips BLOCK_CARD from every case under
    p = 0.70 and the agent silently under-blocks the fraud half of the benchmark.
    """
    ledger = EvidenceLedger(table, TriggerType.RISK_SCORE)
    ledger.post("risk_85_100", True, "score is in the top band", "query:txn_detail(txn_id=1)")
    ledger.post("device_new", True, "new device", "query:device_novelty(txn_id=1)")
    ledger.post("region_novel", True, "novel region", "query:region_novelty(card_id=C1)")
    assert ledger.independent_support() == 3

    state = CaseStateBuilder(ledger).build(
        facts=ScopedFacts(), trigger_type=TriggerType.RISK_SCORE, exposure_usd=0.0
    )
    assert state.independent_signals == 3
    assert state.rests_on_one_signal is False


def test_the_verdict_follows_the_policy_stopping_bounds(table):
    high = EvidenceLedger(table, TriggerType.CUSTOMER_REPORT)
    for _ in range(6):
        high.post("risk_85_100", True, "top band", "ref")
        high.post("proxy_present", True, "proxy", "ref")
        high.post("country_not_home", True, "foreign", "ref")
    assert CaseStateBuilder(high).verdict() is Verdict.FRAUD

    middling = EvidenceLedger(table, TriggerType.RISK_SCORE)
    middling.post("risk_50_70", True, "middle band", "ref")
    assert CaseStateBuilder(middling).verdict() is Verdict.UNCERTAIN


def test_r5_needs_three_small_authorisations(table):
    builder = CaseStateBuilder(EvidenceLedger(table))
    two = builder.build(
        facts=ScopedFacts(testing=CardTestingProbe(small_online_auths_1h=2)),
        trigger_type=TriggerType.RISK_SCORE,
        exposure_usd=0.0,
    )
    assert two.card_testing_sequence is False

    three = builder.build(
        facts=ScopedFacts(testing=CardTestingProbe(small_online_auths_1h=3)),
        trigger_type=TriggerType.RISK_SCORE,
        exposure_usd=0.0,
    )
    assert three.card_testing_sequence is True


def test_a_missing_cleared_purchase_is_not_a_cleared_purchase(table):
    # largest_purchase_after is None when nothing followed. Reading the raw
    # -1.797e308 sentinel as a number would make this False by luck, not design.
    builder = CaseStateBuilder(EvidenceLedger(table))
    state = builder.build(
        facts=ScopedFacts(
            testing=CardTestingProbe(small_online_auths_1h=3, largest_purchase_after=None)
        ),
        trigger_type=TriggerType.RISK_SCORE,
        exposure_usd=0.0,
    )
    assert state.card_testing_cleared_over_100 is False


def test_r7_needs_three_distinct_months(table):
    builder = CaseStateBuilder(EvidenceLedger(table))
    twice = builder.build(
        facts=ScopedFacts(recurring=RecurringChargeProbe(matching_charges=2, distinct_months=2)),
        trigger_type=TriggerType.CUSTOMER_REPORT,
        exposure_usd=0.0,
    )
    assert twice.recurring_match is False

    monthly = builder.build(
        facts=ScopedFacts(recurring=RecurringChargeProbe(matching_charges=57, distinct_months=6)),
        trigger_type=TriggerType.CUSTOMER_REPORT,
        exposure_usd=0.0,
    )
    assert monthly.recurring_match is True


def test_a_generic_device_profile_is_not_a_shared_origin(table):
    """R6 implies CREATE_CASE + FILE_REPORT, so this gate stops a false SAR."""
    generic = RingExpansion.model_validate(
        {
            "devices_used": [
                {"DALL.device_key": "Dwide", "DALL.label": "Windows | chrome", "DALL.n_cards": 842}
            ],
            "connected_cards": [{"C2.card_id": "C00001-K1", "C2.customer_id": "C00001"}],
            "devices_total": 1,
            "devices_specific_enough": 0,
        }
    )
    state = CaseStateBuilder(EvidenceLedger(table)).build(
        facts=ScopedFacts(ring=generic), trigger_type=TriggerType.RISK_SCORE, exposure_usd=0.0
    )
    assert state.shared_origin is False


def test_a_specific_device_profile_is_a_shared_origin_and_names_itself(table):
    specific = RingExpansion.model_validate(
        {
            "devices_used": [
                {"DALL.device_key": "Dtight", "DALL.label": "iPad | safari", "DALL.n_cards": 4}
            ],
            "connected_cards": [
                {"C2.card_id": "C00877-K1", "C2.customer_id": "C00877", "C2.@max_risk": 0.88}
            ],
            "devices_total": 1,
            "devices_specific_enough": 1,
        }
    )
    state = CaseStateBuilder(EvidenceLedger(table)).build(
        facts=ScopedFacts(ring=specific), trigger_type=TriggerType.RISK_SCORE, exposure_usd=0.0
    )
    assert state.shared_origin is True
    assert "iPad" in state.shared_origin_kind
    assert state.other_customer_fraud is True, "a different customer's card at risk 0.88"


def test_r10_counts_distinct_cards_not_cases(table):
    # Five closed cases on one card is ONE compromised card. Counting cases would
    # make BLOCK_ALL_CARDS reachable off a single card's history.
    history = CustomerCaseHistory.model_validate(
        {
            "cases": [
                {"R.case_id": f"CC-000{i}", "R.card_id": CARD, "R.outcome": "confirmed_fraud"}
                for i in range(1, 6)
            ],
            "confirmed_fraud_cases": 5,
            "cleared_cases": 1,
        }
    )
    state = CaseStateBuilder(EvidenceLedger(table)).build(
        facts=ScopedFacts(history=history),
        trigger_type=TriggerType.CUSTOMER_REPORT,
        exposure_usd=0.0,
    )
    assert state.cards_with_confirmed_fraud == 1


def test_an_undocumented_pattern_sets_the_r9_flag(table):
    state = CaseStateBuilder(EvidenceLedger(table)).build(
        facts=ScopedFacts(pattern=Pattern.UNDOCUMENTED),
        trigger_type=TriggerType.RISK_SCORE,
        exposure_usd=0.0,
    )
    assert state.undocumented_pattern is True


# ═══ the evidence simulator ══════════════════════════════════════════════════


def test_a_recurring_charge_produces_a_confirmation():
    sim = EvidenceSimulator(
        recurring=RecurringChargeProbe(matching_charges=57, distinct_months=6),
        refs={"recurring_charge_probe": "query:recurring_charge_probe(card_id=C08623-K2)"},
    )
    out = sim.simulate(RequestType.CUSTOMER_VALIDATION)
    assert out.branch is CustomerResponse.CONFIRMED
    assert out.log_lr < 0, "a confirmation argues for legitimacy"
    assert out.assumption_basis, "a branch with no basis is a guess"


def test_a_reliable_denier_produces_a_denial():
    sim = EvidenceSimulator(
        history=CustomerCaseHistory(customer_reports_confirmed_fraud=5, confirmed_fraud_cases=5),
        refs={"customer_case_history": "query:customer_case_history(customer_id=C08623)"},
    )
    out = sim.simulate(RequestType.CUSTOMER_VALIDATION)
    assert out.branch is CustomerResponse.DENIED
    assert out.log_lr > 0


def test_evidence_pulling_both_ways_produces_no_reply():
    """The HHG-003 shape: a cardholder right five times out of five, disputing
    a charge that matches their own six-month subscription. Neither side wins,
    and claiming one would be the simulator reasoning backwards."""
    sim = EvidenceSimulator(
        recurring=RecurringChargeProbe(matching_charges=57, distinct_months=6),
        history=CustomerCaseHistory(customer_reports_confirmed_fraud=5),
        refs={
            "recurring_charge_probe": "query:recurring_charge_probe(card_id=C08623-K2)",
            "customer_case_history": "query:customer_case_history(customer_id=C08623)",
        },
    )
    out = sim.simulate(RequestType.CUSTOMER_VALIDATION)
    assert out.branch is CustomerResponse.NO_REPLY
    assert out.log_lr == 0.0
    assert len(out.assumption_basis) == 2, "both readings are cited"


def test_nothing_measured_produces_no_reply_not_a_coin_flip():
    out = EvidenceSimulator().simulate(RequestType.CUSTOMER_VALIDATION)
    assert out.branch is CustomerResponse.NO_REPLY
    assert out.log_lr == 0.0


def test_step_up_passes_on_a_device_the_card_knows():
    device = DeviceNovelty.model_validate(
        {
            "flags": [{"R.txn_id": TXN, "R.device_key": "Dknown"}],
            "prior_txns_this_device_on_card": 14,
        }
    )
    out = EvidenceSimulator(
        device=device, refs={"device_novelty": "query:device_novelty(txn_id=1)"}
    ).simulate(RequestType.STEP_UP_AUTH)
    assert out.branch is CustomerResponse.STEP_UP_PASSED
    assert out.log_lr < 0


def test_step_up_fails_on_an_unrecognised_device():
    device = DeviceNovelty.model_validate(
        {
            "flags": [{"R.txn_id": TXN, "R.device_key": "Dnew", "R.device_new": "New"}],
            "prior_txns_this_device_on_card": 0,
        }
    )
    out = EvidenceSimulator(device=device).simulate(RequestType.STEP_UP_AUTH)
    assert out.branch is CustomerResponse.STEP_UP_FAILED
    assert out.log_lr > 0


def test_the_simulator_is_deterministic():
    # Two runs of the benchmark must agree, or the initial-to-final story is noise.
    def build() -> EvidenceSimulator:
        return EvidenceSimulator(
            recurring=RecurringChargeProbe(matching_charges=57, distinct_months=6)
        )

    first = build().simulate(RequestType.CUSTOMER_VALIDATION)
    second = build().simulate(RequestType.CUSTOMER_VALIDATION)
    assert first == second


def test_every_branch_carries_the_sentence_the_answer_file_needs():
    for sim in (
        EvidenceSimulator(recurring=RecurringChargeProbe(matching_charges=57, distinct_months=6)),
        EvidenceSimulator(history=CustomerCaseHistory(customer_reports_confirmed_fraud=5)),
        EvidenceSimulator(),
    ):
        out = sim.simulate(RequestType.CUSTOMER_VALIDATION)
        assert out.assumed_response.strip()
        assert out.claim.strip()
