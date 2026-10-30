"""The ledger's arithmetic, pinned.

The five ported tests come from ``tests/test_policy.py`` in v1 and are unchanged
but for the injected table. The rest exist because v1 shipped two silent
defaults and one asymmetric cap, and each of those three is a way the fraud
probability can be wrong with nothing downstream able to tell.
"""

from __future__ import annotations

import json
import math

import pytest

from sentinel.domain.enums import EvidenceSource, TriggerType
from sentinel.domain.errors import UnknownFeature, UnknownTrigger
from sentinel.evidence.ledger import GROUP_CAP, EvidenceLedger
from sentinel.evidence.table import DEFAULT_ELT_PATH, EvidenceLikelihoodTable

REF = "query:device_novelty(txn_id=X)"


@pytest.fixture(scope="module")
def table() -> EvidenceLikelihoodTable:
    """The shipped table, by explicit path — unit tests never touch Settings."""
    return EvidenceLikelihoodTable(DEFAULT_ELT_PATH)


def logit(p: float) -> float:
    return math.log(p / (1.0 - p))


# ── ported from v1 tests/test_policy.py ──────────────────────────────────────


def test_ledger_prior_follows_the_trigger(table):
    assert (
        EvidenceLedger(table, "customer_report").prior > EvidenceLedger(table, "risk_score").prior
    )


def test_ledger_moves_with_evidence_and_records_why(table):
    led = EvidenceLedger(table, "risk_score")
    start = led.p
    led.post("risk_85_100", True, "model scored 0.90", "query:txn_detail(txn_id=X)", ["X"])
    assert led.p > start
    assert led.postings[0].p_before == pytest.approx(start, abs=1e-4)
    assert led.trajectory[0] == pytest.approx(round(led.prior, 4))


def test_ledger_caps_correlated_evidence(table):
    """Three phrasings of one device finding must not multiply."""
    led = EvidenceLedger(table, "risk_score")
    for feature in ("device_new", "device_never_used_on_card", "proxy_present"):
        led.post(feature, True, "device signal", REF, ["X"])
    assert any(p.capped for p in led.postings)
    assert led.independent_support() == 1  # one group, however many postings


def test_ledger_counts_independent_groups_not_postings(table):
    led = EvidenceLedger(table, "risk_score")
    led.post("risk_85_100", True, "score", "ref", [])
    led.post("device_new", True, "device", "ref", [])
    assert led.independent_support() == 2


def test_absence_is_recorded_as_evidence(table):
    led = EvidenceLedger(table, "customer_report")
    start = led.p
    led.post("device_new", False, "device is not new for this account", "ref", [])
    assert led.p != start
    assert led.postings[0].present is False
    assert led.postings[0].as_evidence().claim


# ── the worked example, pinned to the fitted numbers ─────────────────────────


def test_the_device_group_worked_example(table):
    """Every number here comes from the fitted table and must not drift."""
    led = EvidenceLedger(table, TriggerType.RISK_SCORE)
    assert led.prior == 0.25

    first = led.post("device_new", True, "device is new to this account", REF, ["X"])
    second = led.post(
        "device_never_used_on_card", True, "device never used on this card", REF, ["X"]
    )
    third = led.post("proxy_present", True, "the session is behind a proxy", REF, ["X"])

    assert (first.lr, second.lr, third.lr) == (1.3006, 1.7507, 2.6001)
    assert (first.capped, second.capped) == (False, False)
    assert third.capped is True

    # The third posting is trimmed to whatever is left of the group's 1.2.
    assert third.log_lr == pytest.approx(
        GROUP_CAP - (math.log(1.3006) + math.log(1.7507)), abs=5e-5
    )
    assert led.group_totals["device"] == pytest.approx(GROUP_CAP, abs=1e-12)
    assert led.independent_support() == 1

    assert led.p == pytest.approx(0.5253, abs=1e-4)
    assert led.trajectory == [0.25, 0.3024, 0.4315, 0.5253]


# ── defect Q2: the cap binds the total, not the increment ────────────────────


def test_contrary_evidence_is_not_over_suppressed(table):
    """v1 clamped the increment by the room left, so this group settled at -0.2.

    With the group at +0.5 an incoming -2.0 has 0.7 of room by that rule, which
    left the total at -0.2 — exonerating evidence arriving second was all but
    erased, on exactly the legitimate cases it mattered for.
    """
    led = EvidenceLedger(table, "risk_score")
    led.post_judgement(
        "device", 0.5, "one incriminating device reading", "evidence_request:1", cap_multiplier=1.0
    )
    contrary = led.post_judgement(
        "device", -2.0, "the device is the customer's own", "evidence_request:2", cap_multiplier=1.0
    )

    assert led.group_totals["device"] == pytest.approx(-GROUP_CAP, abs=1e-12)
    assert led.group_totals["device"] != pytest.approx(-0.2, abs=1e-6)
    assert contrary.log_lr == pytest.approx(-1.7, abs=1e-9)
    assert contrary.capped is True
    assert logit(led.p) == pytest.approx(logit(0.25) - GROUP_CAP, abs=1e-9)


def test_capping_is_symmetric_in_both_directions(table):
    up = EvidenceLedger(table, "risk_score")
    down = EvidenceLedger(table, "risk_score")
    up.post_judgement("device", 9.0, "strongly incriminating", "ref", cap_multiplier=1.0)
    down.post_judgement("device", -9.0, "strongly exonerating", "ref", cap_multiplier=1.0)
    assert up.group_totals["device"] == pytest.approx(GROUP_CAP)
    assert down.group_totals["device"] == pytest.approx(-GROUP_CAP)


def test_a_judgement_is_capped_wider_than_a_fitted_feature(table):
    led = EvidenceLedger(table, "risk_score")
    posting = led.post_judgement(
        "response", 5.0, "the customer denies the charge", "evidence_request:1"
    )
    assert led.group_totals["response"] == pytest.approx(GROUP_CAP * 1.5)
    assert posting.log_lr == pytest.approx(1.8)
    assert posting.source is EvidenceSource.CUSTOMER


# ── absence moves the number the other way ───────────────────────────────────


def test_lr_absent_moves_the_probability_the_other_way(table):
    present = EvidenceLedger(table, "risk_score")
    absent = EvidenceLedger(table, "risk_score")
    start = present.prior

    present.post("device_new", True, "device is new", REF, ["X"])
    absent.post("device_new", False, "device is known to this account", REF, ["X"])

    assert present.p > start > absent.p
    assert absent.postings[0].lr == 0.9585  # lr_absent from the fitted table
    assert absent.postings[0].log_lr < 0


def test_an_absent_feature_can_still_be_incriminating(table):
    """lr_absent is not always below 1: a score that is *not* low argues up."""
    led = EvidenceLedger(table, "risk_score")
    led.post("risk_00_30", False, "the model score is not in the bottom band", "ref", [])
    assert led.p > led.prior


# ── defect Q1 and the typo guard: nothing defaults silently ──────────────────


def test_analyst_request_has_a_stated_prior_of_half(table):
    assert table.prior_for(TriggerType.ANALYST_REQUEST) == 0.50
    assert EvidenceLedger(table, TriggerType.ANALYST_REQUEST).prior == 0.50


def test_the_analyst_request_prior_is_supplied_by_code_not_by_a_default(table):
    """0.50 must be the stated value, not the shape of a missing key.

    The fitted file has no analyst_request row — if the ledger simply defaulted,
    an unfitted trigger would get 0.50 too instead of raising.
    """
    fitted = json.loads(DEFAULT_ELT_PATH.read_text(encoding="utf-8"))["trigger_priors"]
    assert "analyst_request" not in fitted
    assert "analyst_request" in table.trigger_types
    with pytest.raises(UnknownTrigger):
        table.prior_for("some_other_trigger")


def test_an_unknown_trigger_raises_rather_than_assuming_half(table):
    with pytest.raises(UnknownTrigger) as excinfo:
        EvidenceLedger(table, "manager_hunch")
    assert "manager_hunch" in str(excinfo.value)


def test_an_unknown_feature_raises_rather_than_posting_nothing(table):
    led = EvidenceLedger(table, "risk_score")
    with pytest.raises(UnknownFeature):
        led.post("device_is_new", True, "typo of device_new", REF, ["X"])
    assert led.postings == ()
    assert led.p == led.prior

    with pytest.raises(UnknownFeature):
        table.lookup("device_is_new", True)
    with pytest.raises(UnknownFeature):
        table.group_of("device_is_new")
    assert table.has("device_new") is True
    assert table.has("device_is_new") is False


# ── the outputs the answer file and the policy engine read ───────────────────


def test_evidence_is_the_four_field_answer_shape(table):
    led = EvidenceLedger(table, "risk_score")
    led.post("device_new", True, "device is new to this account", REF, ["T1", "D1"])
    led.post_judgement("response", 0.9, "the customer denies the charge", "evidence_request:1")

    graph_item, customer_item = led.evidence()
    assert graph_item.source is EvidenceSource.GRAPH
    assert graph_item.ref == REF
    assert graph_item.entity_ids == ["T1", "D1"]
    assert customer_item.source is EvidenceSource.CUSTOMER
    assert set(graph_item.model_dump()) == {"claim", "source", "ref", "entity_ids"}


def test_top_drivers_ranks_by_how_far_it_moved_the_number(table):
    led = EvidenceLedger(table, "risk_score")
    led.post("device_new", True, "device is new", REF, ["X"])
    led.post("risk_85_100", True, "model scored 0.90", "query:txn_detail(txn_id=X)", ["X"])
    drivers = led.top_drivers(1)
    assert [p.feature for p in drivers] == ["risk_85_100"]


def test_group_totals_is_a_copy_and_cannot_be_written_through(table):
    led = EvidenceLedger(table, "risk_score")
    led.post("device_new", True, "device is new", REF, ["X"])
    led.group_totals["device"] = 99.0
    assert led.group_totals["device"] != 99.0


def test_a_group_that_barely_moved_is_not_independent_support(table):
    led = EvidenceLedger(table, "risk_score")
    led.post_judgement("device", 0.01, "a hair of a signal", "ref")
    assert led.independent_support() == 0


def test_explain_names_the_prior_the_trigger_and_every_capped_posting(table):
    led = EvidenceLedger(table, "risk_score")
    for feature in ("device_new", "device_never_used_on_card", "proxy_present"):
        led.post(feature, True, "device signal", REF, ["X"])
    text = led.explain()
    assert "risk_score" in text
    assert "[capped]" in text
    assert text.count("[capped]") == 1


# ── snapshots ────────────────────────────────────────────────────────────────


def test_a_snapshot_does_not_move_when_the_ledger_does(table):
    """`next_best_actions.initial` is evaluated against a frozen reading.

    The regression this pins: the initial recommendation read `ledger.p` at
    decide time, by which point the requested evidence had already been posted.
    HHG-006's requested step-up moved the number from 0.8111 to 0.5883 and the
    *pre*-request recommendation was computed at 0.5883 — so `initial` and
    `final` could never disagree about the probability, and on 18 of 20 cases
    they did not disagree at all.
    """
    ledger = EvidenceLedger(table, TriggerType.RISK_SCORE)
    ledger.post_judgement(group="a", log_lr=1.5, claim="incriminating", ref="alert:X")
    before = ledger.snapshot()

    ledger.post_judgement(group="response", log_lr=-1.5, claim="step-up passed", ref="alert:X")

    assert before.p > ledger.p, "the second posting must have moved the number"
    assert before.postings == 1 and len(ledger.postings) == 2
    assert before.independent_support == 1
    assert ledger.independent_support() == 2


def test_a_state_built_from_a_snapshot_reports_the_snapshots_probability(table):
    from sentinel.agents.state_builder import CaseStateBuilder, ScopedFacts

    ledger = EvidenceLedger(table, TriggerType.RISK_SCORE)
    ledger.post_judgement(group="a", log_lr=2.0, claim="incriminating", ref="alert:X")
    before = ledger.snapshot()
    ledger.post_judgement(group="response", log_lr=-3.0, claim="exonerating", ref="alert:X")

    builder = CaseStateBuilder(ledger)
    initial = builder.build(
        facts=ScopedFacts(),
        trigger_type=TriggerType.RISK_SCORE,
        exposure_usd=0.0,
        snapshot=before,
    )
    final = builder.build(
        facts=ScopedFacts(), trigger_type=TriggerType.RISK_SCORE, exposure_usd=0.0
    )
    assert initial.fraud_probability == round(before.p, 4)
    assert final.fraud_probability == round(ledger.p, 4)
    assert initial.fraud_probability > final.fraud_probability


def test_a_narrower_cap_on_a_later_posting_cannot_invert_its_sign(table):
    """The cap belongs to the group, not to the call that posts into it.

    The regression this pins: `cap` arrived per call — 1.2 from a fitted
    feature, 1.8 from a judgement — and a narrower one arriving after the group
    total had passed it made `clamped - used` negative whatever the sign of the
    increment. An incriminating +0.4 was applied as -0.5 and recorded that way
    in the trace the console renders.
    """
    ledger = EvidenceLedger(table, TriggerType.RISK_SCORE)
    ledger.post_judgement(group="device", log_lr=1.7, claim="wide-cap judgement", ref=REF)
    second = ledger.post_judgement(
        group="device",
        log_lr=0.4,
        claim="second incriminating judgement",
        ref=REF,
        cap_multiplier=1.0,
    )
    assert second.log_lr >= 0.0, "an incriminating posting moved the probability down"
    assert ledger.group_totals["device"] <= GROUP_CAP * 1.5 + 1e-9


# ── the score is not counted twice ───────────────────────────────────────────


def test_a_score_alert_does_not_count_its_own_score_twice(table):
    """The prior for a score alert already says the model flagged it.

    Guide.md: "Above 0.7, most flagged transactions turn out to be
    legitimate." Posting the 0.70-0.85 band's own LR of 8.22 on top of a prior
    that exists *because* the score was high carried eight of the eleven
    score-triggered benchmark cases past 0.85 on the strength of the score that
    raised them. The band is re-centred on what the alert already implied.
    """
    scored = EvidenceLedger(table, TriggerType.RISK_SCORE)
    posting = scored.post("risk_70_85", True, "the model scored it 0.79", REF, [])
    assert posting.log_lr == pytest.approx(0.0, abs=1e-9)
    assert scored.p == pytest.approx(scored.prior, abs=1e-9)


def test_a_score_above_the_alerting_band_still_argues_up(table):
    scored = EvidenceLedger(table, TriggerType.RISK_SCORE)
    posting = scored.post("risk_85_100", True, "the model scored it 0.95", REF, [])
    assert posting.log_lr > 0
    assert scored.p > scored.prior


def test_a_score_below_the_alerting_band_argues_the_model_was_reaching(table):
    scored = EvidenceLedger(table, TriggerType.RISK_SCORE)
    posting = scored.post("risk_30_50", True, "the model scored it 0.42", REF, [])
    assert posting.log_lr < 0
    assert scored.p < scored.prior


def test_a_customer_report_gets_the_full_ratio_because_the_score_is_news(table):
    """The cardholder raised this alert, so the model's score is independent."""
    reported = EvidenceLedger(table, TriggerType.CUSTOMER_REPORT)
    posting = reported.post("risk_70_85", True, "the model scored it 0.79", REF, [])
    # The raw ratio is 8.22 — log 2.107 — which the group cap then clamps to
    # 1.2. What matters is that nothing was subtracted before the cap saw it.
    assert posting.log_lr == pytest.approx(GROUP_CAP)
    assert posting.capped is True
    assert math.log(table.lookup("risk_70_85", True)) > GROUP_CAP


def test_the_channel_family_is_one_observation_not_four(table):
    """channel_online, dist1_missing, m_flags and device_found are one fact.

    Vesta populates the M flags and the device record only for online
    transactions, and dist1 is missing precisely when there is no card-present
    distance. Measured over the benchmark, the four fitted groups contributed
    +1.84 log-odds on every case that came back fraud and -1.45 on every case
    that came back legitimate: a 27x swing for one fact.
    """
    for feature in ("channel_online", "dist1_missing", "m_flags_all_true", "device_found"):
        assert table.group_of(feature) == "channel", feature
    # The device's own signals are a different observation and stay apart.
    assert table.group_of("device_new") == "device"
    assert table.group_of("device_never_used_on_card") == "device"

    ledger = EvidenceLedger(table, TriggerType.RISK_SCORE)
    for feature in ("channel_online", "dist1_missing", "m_flags_all_true", "device_found"):
        ledger.post(feature, True, "online", REF, [])
    assert abs(ledger.group_totals["channel"]) <= GROUP_CAP + 1e-9
