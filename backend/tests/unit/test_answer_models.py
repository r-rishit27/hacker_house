"""Contract tests for the answer models and the alert loader.

The strongest test here is `test_golden_fixture_round_trips`: the real
`cases/HHG-003.json`, the one answer file written by hand and checked against the
graph, must parse and re-serialise to an identical dict. If that breaks, the
models have drifted from the thing the benchmark actually grades.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from sentinel.domain.alert import (
    ALERT_LAG_MAX_HOURS,
    ALERT_LAG_MIN_HOURS,
    MISSING_RISK_SCORE,
    Alert,
    CasePackLoader,
    TriggerContext,
)
from sentinel.domain.answer import (
    ActionRecommendation,
    AnswerFile,
    Case,
    Evidence,
    EvidenceRequest,
    NextBestActions,
    SarReport,
    count_sentences,
)
from sentinel.domain.enums import (
    Action,
    CaseStatus,
    EvidenceSource,
    Pattern,
    RequestType,
    Route,
    TriggerType,
    Verdict,
)

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "cases" / "HHG-003.json"
CASE_PACK = ROOT / "case_pack.csv"
STAGED_ALERTS = ROOT / "data" / "staging" / "alerts.csv"

# Flagged-transaction timestamps for all twenty alerts, measured from
# data/staging/transactions.csv.gz. Inlined so the lag invariant is testable
# without reading a 30 MB gzip in a unit test.
FLAGGED_TXN_TS = {
    "HHG-001": "2016-12-04 19:55:28",
    "HHG-002": "2016-11-22 17:27:07",
    "HHG-003": "2016-12-10 13:01:21",
    "HHG-004": "2016-12-29 01:53:54",
    "HHG-005": "2016-12-07 21:38:37",
    "HHG-006": "2016-11-21 20:30:00",
    "HHG-007": "2016-12-05 00:46:14",
    "HHG-008": "2016-12-19 22:08:56",
    "HHG-009": "2016-12-28 12:10:53",
    "HHG-010": "2016-12-02 15:18:27",
    "HHG-011": "2016-12-29 03:27:44",
    "HHG-012": "2016-12-18 04:00:31",
    "HHG-013": "2016-12-09 02:39:29",
    "HHG-014": "2016-11-22 16:11:00",
    "HHG-015": "2016-11-17 14:03:36",
    "HHG-016": "2016-12-11 22:39:08",
    "HHG-017": "2016-11-11 23:46:24",
    "HHG-018": "2016-11-27 13:41:26",
    "HHG-019": "2016-12-01 17:28:53",
    "HHG-020": "2016-12-03 06:04:26",
}

# The real Transaction.risk_score for the nine alerts whose alert-level score is
# the -1 sentinel. Measured from the same file.
REAL_SCORE_WHERE_ALERT_HAS_NONE = {
    "HHG-003": 0.40,
    "HHG-004": 0.34,
    "HHG-006": 0.25,
    "HHG-008": 0.38,
    "HHG-009": 0.28,
    "HHG-011": 0.39,
    "HHG-014": 0.05,
    "HHG-016": 0.37,
    "HHG-018": 0.48,
}

# Guide.md's own worked example, seven sentences, several with dollar figures.
GUIDE_NARRATIVE = (
    "On 2016-11-14 between 09:12 and 09:52, card C00377-K1 belonging to customer C00377 was "
    "used for three online authorizations of $1.10, $2.40, and $0.95 followed at 10:31 by a "
    "$259.98 online purchase under a product code the cardholder had never used. All four "
    "transactions came from a device profile marked New for this account, previously recorded "
    "on closed case CC-0141 and on card C00877-K1 on 2016-11-12. The cardholder, contacted the "
    "same day, stated they did not make these purchases and remained in possession of the card. "
    "The sequence of small authorizations followed by a larger purchase is consistent with "
    "testing of a stolen card number prior to use. The shared device indicates a common actor "
    "across at least two cardholders. Total unauthorized amount: $268.43. Card blocked and "
    "scheduled for reissue; card C00877-K1 placed under monitoring."
)


def narrative(sentences: int) -> str:
    return " ".join(f"Finding {i} on the disputed activity." for i in range(sentences))


def an_evidence(**overrides: object) -> Evidence:
    base: dict[str, object] = {
        "claim": "The disputed charge is a $49.00 card-present purchase.",
        "source": EvidenceSource.GRAPH,
        "ref": "query:txn_detail(txn_id=3530164)",
        "entity_ids": ["3530164"],
    }
    return Evidence(**(base | overrides))  # type: ignore[arg-type]


def a_case(**overrides: object) -> Case:
    base: dict[str, object] = {
        "status": CaseStatus.ESCALATED,
        "verdict": Verdict.UNCERTAIN,
        "fraud_probability": 0.32,
        "pattern": Pattern.NONE,
        "affected_txn_ids": ["3530164"],
        "first_suspicious_txn_id": "3530164",
        "exposure_usd": 49.0,
        "evidence": [an_evidence()],
        "summary": "Cardholder disputes a $49.00 card-present purchase.",
    }
    return Case(**(base | overrides))  # type: ignore[arg-type]


def a_recommendation(
    action: Action = Action.CREATE_CASE, **overrides: object
) -> ActionRecommendation:
    base: dict[str, object] = {
        "action": action,
        "route": Route.AUTO,
        "reason": "3a: a case is opened",
    }
    return ActionRecommendation(**(base | overrides))  # type: ignore[arg-type]


def a_sar(**overrides: object) -> SarReport:
    base: dict[str, object] = {"file": False, "reason": "3a: no report."}
    return SarReport(**(base | overrides))  # type: ignore[arg-type]


def an_answer(**overrides: object) -> AnswerFile:
    recs = [a_recommendation()]
    base: dict[str, object] = {
        "case_id": "HHG-003",
        "case": a_case(),
        "evidence_requests": [],
        "next_best_actions": NextBestActions(initial=recs, final=list(recs)),
        "sar": a_sar(),
        "stop_reason": "Policy 6: the decisive question is settled.",
        "tool_calls": 17,
        "tokens": 0,
        "latency_s": 1080.0,
    }
    return AnswerFile(**(base | overrides))  # type: ignore[arg-type]


# ── The golden fixture ───────────────────────────────────────────────────────


def test_golden_fixture_round_trips() -> None:
    raw = json.loads(GOLDEN.read_text(encoding="utf-8"))
    answer = AnswerFile.model_validate(raw)
    assert answer.model_dump(mode="json") == raw


def test_golden_fixture_keeps_key_order() -> None:
    raw = json.loads(GOLDEN.read_text(encoding="utf-8"))
    dumped = AnswerFile.model_validate(raw).model_dump(mode="json")
    assert list(dumped) == list(raw)
    assert list(dumped["case"]) == list(raw["case"])
    assert list(dumped["sar"]) == list(raw["sar"])
    assert list(dumped["next_best_actions"]) == list(raw["next_best_actions"])
    assert list(dumped["case"]["evidence"][0]) == list(raw["case"]["evidence"][0])
    assert list(dumped["evidence_requests"][0]) == list(raw["evidence_requests"][0])
    assert list(dumped["next_best_actions"]["initial"][0]) == list(
        raw["next_best_actions"]["initial"][0]
    )


def test_golden_fixture_to_json_reparses_identically() -> None:
    raw = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert json.loads(AnswerFile.model_validate(raw).to_json()) == raw


def test_field_counts_are_contractual() -> None:
    dumped = an_answer(
        evidence_requests=[
            EvidenceRequest(
                type=RequestType.CUSTOMER_VALIDATION,
                asked_after_step=5,
                assumed_response="Cardholder confirms presence in the region.",
            )
        ]
    ).model_dump(mode="json")
    assert len(dumped) == 9
    assert len(dumped["case"]) == 15
    assert len(dumped["evidence_requests"][0]) == 3
    assert len(dumped["next_best_actions"]) == 3
    assert len(dumped["sar"]) == 6
    assert len(dumped["case"]["evidence"][0]) == 4
    assert len(dumped["next_best_actions"]["initial"][0]) == 3


def test_unknown_key_is_rejected_everywhere() -> None:
    raw = json.loads(GOLDEN.read_text(encoding="utf-8"))
    raw["confidence"] = 0.9
    with pytest.raises(ValidationError, match="confidence"):
        AnswerFile.model_validate(raw)


# ── sar_agrees_with_actions ──────────────────────────────────────────────────


def test_sar_agrees_with_actions_accepts_filing_with_file_report() -> None:
    recs = [a_recommendation(Action.FILE_REPORT, route=Route.L2, reason="R2: confirmed fraud")]
    answer = an_answer(
        next_best_actions=NextBestActions(initial=recs, final=list(recs)),
        sar=a_sar(
            file=True,
            reason="R2: confirmed unauthorized use",
            narrative=GUIDE_NARRATIVE,
            subjects=["C08623"],
            total_amount_usd=49.0,
            activity_dates=["2016-12-10", "2016-12-10"],
        ),
    )
    assert answer.sar.file is True


def test_sar_agrees_with_actions_rejects_filing_without_file_report() -> None:
    with pytest.raises(ValidationError, match="FILE_REPORT is not in"):
        an_answer(
            sar=a_sar(
                file=True,
                reason="R2",
                narrative=GUIDE_NARRATIVE,
                subjects=["C08623"],
                activity_dates=["2016-12-10", "2016-12-10"],
            )
        )


def test_sar_agrees_with_actions_rejects_file_report_without_filing() -> None:
    recs = [a_recommendation(Action.FILE_REPORT, route=Route.L2, reason="R2")]
    with pytest.raises(ValidationError, match="FILE_REPORT is in"):
        an_answer(next_best_actions=NextBestActions(initial=recs, final=list(recs)))


# ── sar_empty_when_not_filing ────────────────────────────────────────────────


def test_sar_empty_when_not_filing_accepts_the_empty_report() -> None:
    report = a_sar()
    assert (report.narrative, report.subjects, report.total_amount_usd, report.activity_dates) == (
        "",
        [],
        0.0,
        [],
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("narrative", GUIDE_NARRATIVE),
        ("subjects", ["C08623"]),
        ("total_amount_usd", 49.0),
        ("activity_dates", ["2016-12-10", "2016-12-10"]),
    ],
)
def test_sar_empty_when_not_filing_rejects_each_populated_field(field: str, value: object) -> None:
    with pytest.raises(ValidationError, match=field):
        a_sar(**{field: value})


# ── sar_complete_when_filing ─────────────────────────────────────────────────


def filed_sar(**overrides: object) -> SarReport:
    base: dict[str, object] = {
        "file": True,
        "reason": "R2: confirmed unauthorized use",
        "narrative": GUIDE_NARRATIVE,
        "subjects": ["C00377", "C00377-K1"],
        "total_amount_usd": 268.43,
        "activity_dates": ["2016-11-14", "2016-11-14"],
    }
    return SarReport(**(base | overrides))  # type: ignore[arg-type]


def test_sar_complete_when_filing_accepts_the_guide_example() -> None:
    assert count_sentences(filed_sar().narrative) == 7


def test_sar_complete_when_filing_rejects_an_empty_narrative() -> None:
    with pytest.raises(ValidationError, match="narrative is empty"):
        filed_sar(narrative="")


@pytest.mark.parametrize("sentences", [5, 13])
def test_sar_complete_when_filing_rejects_an_out_of_range_narrative(sentences: int) -> None:
    with pytest.raises(ValidationError, match=f"has {sentences} sentences"):
        filed_sar(narrative=narrative(sentences))


@pytest.mark.parametrize("sentences", [6, 12])
def test_sar_complete_when_filing_accepts_the_boundaries(sentences: int) -> None:
    assert filed_sar(narrative=narrative(sentences)).file is True


def test_sar_complete_when_filing_rejects_empty_subjects() -> None:
    with pytest.raises(ValidationError, match="subjects is empty"):
        filed_sar(subjects=[])


@pytest.mark.parametrize("dates", [[], ["2016-11-14"], ["2016-11-14", "2016-11-14", "2016-11-15"]])
def test_sar_complete_when_filing_needs_exactly_two_dates(dates: list[str]) -> None:
    with pytest.raises(ValidationError, match="activity_dates"):
        filed_sar(activity_dates=dates)


def test_narrative_sentence_count_ignores_decimal_points() -> None:
    # eval/validate.py splits on "." alone and reads this as five sentences.
    assert count_sentences("Total was $268.43. It cleared at 10:31.") == 2


# ── legitimate_is_clean ──────────────────────────────────────────────────────


def test_legitimate_is_clean_accepts_an_empty_episode() -> None:
    answer = an_answer(
        case=a_case(
            verdict=Verdict.LEGITIMATE,
            status=CaseStatus.CLOSED_LEGITIMATE,
            fraud_probability=0.08,
            affected_txn_ids=[],
            first_suspicious_txn_id="",
            exposure_usd=0.0,
        )
    )
    assert answer.case.exposure_usd == 0.0


def test_legitimate_is_clean_rejects_affected_transactions() -> None:
    with pytest.raises(ValidationError, match="affected_txn_ids must be empty"):
        an_answer(case=a_case(verdict=Verdict.LEGITIMATE, status=CaseStatus.CLOSED_LEGITIMATE))


def test_legitimate_is_clean_rejects_non_zero_exposure() -> None:
    with pytest.raises(ValidationError, match="exposure_usd must be 0"):
        an_answer(
            case=a_case(
                verdict=Verdict.LEGITIMATE,
                status=CaseStatus.CLOSED_LEGITIMATE,
                affected_txn_ids=[],
                first_suspicious_txn_id="",
                exposure_usd=49.0,
            )
        )


def test_legitimate_is_clean_rejects_a_filing() -> None:
    recs = [a_recommendation(Action.FILE_REPORT, route=Route.L2, reason="R2")]
    with pytest.raises(ValidationError, match=re.escape("sar.file must be false")):
        an_answer(
            case=a_case(
                verdict=Verdict.LEGITIMATE,
                status=CaseStatus.CLOSED_LEGITIMATE,
                affected_txn_ids=[],
                first_suspicious_txn_id="",
                exposure_usd=0.0,
            ),
            next_best_actions=NextBestActions(initial=recs, final=list(recs)),
            sar=filed_sar(),
        )


# ── undocumented_has_description ─────────────────────────────────────────────


def test_undocumented_requires_a_description() -> None:
    with pytest.raises(ValidationError, match="pattern_description is empty"):
        a_case(pattern=Pattern.UNDOCUMENTED)


def test_undocumented_accepts_a_description() -> None:
    case = a_case(
        pattern=Pattern.UNDOCUMENTED,
        pattern_description="Refunds reversed within the hour across four unrelated cards.",
    )
    assert case.pattern is Pattern.UNDOCUMENTED


def test_a_description_without_undocumented_is_rejected() -> None:
    with pytest.raises(ValidationError, match="pattern_description is set"):
        a_case(pattern=Pattern.CARD_TESTING, pattern_description="Something else entirely.")


# ── final_equals_initial_without_requests ────────────────────────────────────


def test_final_may_differ_when_evidence_was_requested() -> None:
    answer = an_answer(
        evidence_requests=[
            EvidenceRequest(
                type=RequestType.CUSTOMER_VALIDATION,
                asked_after_step=5,
                assumed_response="Cardholder confirms presence in the region.",
            )
        ],
        next_best_actions=NextBestActions(
            initial=[a_recommendation(Action.VERIFY_WITH_CUSTOMER, reason="R1: verify first")],
            final=[a_recommendation(Action.WARN_CUSTOMER, reason="R7: matches own pattern")],
            what_changed="The cardholder placed themselves in the region.",
        ),
    )
    assert answer.next_best_actions.final != answer.next_best_actions.initial


def test_final_must_equal_initial_when_nothing_was_asked() -> None:
    with pytest.raises(ValidationError, match="must equal"):
        an_answer(
            next_best_actions=NextBestActions(
                initial=[a_recommendation(Action.MONITOR_CARD, reason="R1")],
                final=[a_recommendation(Action.BLOCK_CARD, route=Route.L1, reason="R2")],
            )
        )


def test_final_equals_initial_is_deep_on_reason_strings() -> None:
    # Same action, same route, reworded rationale: still a change nobody asked for.
    with pytest.raises(ValidationError, match="including every reason string"):
        an_answer(
            next_best_actions=NextBestActions(
                initial=[a_recommendation(Action.MONITOR_CARD, reason="R1: watch the card")],
                final=[a_recommendation(Action.MONITOR_CARD, reason="R1: keep watching the card")],
            )
        )


# ── first_suspicious_in_affected ─────────────────────────────────────────────


def test_first_suspicious_must_be_in_the_episode() -> None:
    with pytest.raises(ValidationError, match="not listed in affected_txn_ids"):
        a_case(affected_txn_ids=["3530164"], first_suspicious_txn_id="3530056")


def test_first_suspicious_may_be_empty() -> None:
    assert a_case(affected_txn_ids=[], first_suspicious_txn_id="").first_suspicious_txn_id == ""


# ── written_to_graph_has_id ──────────────────────────────────────────────────


def test_written_to_graph_requires_a_case_id() -> None:
    with pytest.raises(ValidationError, match="graph_case_id is empty"):
        a_case(written_to_graph=True)


def test_written_to_graph_accepts_a_case_id() -> None:
    assert a_case(written_to_graph=True, graph_case_id="CASE-HHG-003").graph_case_id


def test_not_written_to_graph_needs_no_id() -> None:
    assert a_case(written_to_graph=False).graph_case_id == ""


# ── probability_in_range ─────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [-0.01, 1.01, 2.0])
def test_probability_out_of_range_is_rejected(value: float) -> None:
    with pytest.raises(ValidationError, match=re.escape("not a number in 0..1")):
        a_case(fraud_probability=value)


@pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
def test_probability_at_the_boundaries_is_accepted(value: float) -> None:
    assert a_case(fraud_probability=value).fraud_probability == value


# ── evidence_non_empty ───────────────────────────────────────────────────────


def test_evidence_must_not_be_empty() -> None:
    with pytest.raises(ValidationError, match=re.escape("case.evidence is empty")):
        a_case(evidence=[])


def test_one_piece_of_evidence_is_enough() -> None:
    assert len(a_case().evidence) == 1


# ── Alert and the case pack ──────────────────────────────────────────────────


@pytest.fixture(scope="module")
def alerts() -> list[Alert]:
    return CasePackLoader(CASE_PACK).load()


def test_the_case_pack_holds_twenty_alerts(alerts: list[Alert]) -> None:
    assert len(alerts) == 20
    assert [a.alert_id for a in alerts] == [f"HHG-{i:03d}" for i in range(1, 21)]


def test_the_trigger_mix_is_eleven_eight_one(alerts: list[Alert]) -> None:
    counts = {trigger: 0 for trigger in TriggerType}
    for alert in alerts:
        counts[alert.trigger_type] += 1
    assert counts == {
        TriggerType.RISK_SCORE: 11,
        TriggerType.CUSTOMER_REPORT: 8,
        TriggerType.ANALYST_REQUEST: 1,
    }


def test_nine_alerts_carry_no_score_and_none_of_them_is_a_number(alerts: list[Alert]) -> None:
    without = [a.alert_id for a in alerts if a.risk_score is None]
    assert len(without) == 9
    assert set(without) == set(REAL_SCORE_WHERE_ALERT_HAS_NONE)
    assert all(not a.has_model_score for a in alerts if a.alert_id in set(without))
    # Every alert that does quote a score quotes a probability, not a sentinel.
    assert all(0.0 <= a.risk_score <= 1.0 for a in alerts if a.risk_score is not None)


def test_every_scoreless_alert_is_a_report_or_a_request(alerts: list[Alert]) -> None:
    assert all(a.trigger_type is not TriggerType.RISK_SCORE for a in alerts if a.risk_score is None)


def test_the_staged_alerts_file_uses_the_minus_one_sentinel(alerts: list[Alert]) -> None:
    staged = CasePackLoader(STAGED_ALERTS).load_by_id()
    assert len(staged) == 20
    for alert in alerts:
        assert staged[alert.alert_id].risk_score == alert.risk_score
        assert staged[alert.alert_id].status == "new"


@pytest.mark.parametrize("raw", ["", "  ", "-1", "-1.0", -1, -1.0, -1.797693134862316e308, None])
def test_the_missing_score_sentinel_parses_to_none(raw: object) -> None:
    alert = Alert.model_validate(
        {
            "alert_id": "HHG-003",
            "opened_at": "2016-12-10 15:01:21",
            "trigger_type": "customer_report",
            "trigger_text": "Customer message.",
            "flagged_txn_id": "3530164",
            "card_id": "C08623-K2",
            "customer_id": "C08623",
            "risk_score": raw,
        }
    )
    assert alert.risk_score is None
    assert alert.has_model_score is False
    assert MISSING_RISK_SCORE == -1.0


def test_the_alert_always_lags_the_transaction_by_one_to_six_hours(alerts: list[Alert]) -> None:
    for alert in alerts:
        lag = alert.lag_from(datetime.fromisoformat(FLAGGED_TXN_TS[alert.alert_id]))
        assert timedelta(hours=ALERT_LAG_MIN_HOURS) <= lag <= timedelta(hours=ALERT_LAG_MAX_HOURS)
        assert lag != timedelta(0), "opened_at is never the transaction time"


def test_the_window_anchor_is_the_flagged_transaction(alerts: list[Alert]) -> None:
    assert all(a.window_anchor_txn_id == a.flagged_txn_id for a in alerts)


def test_a_missing_id_column_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "broken.csv"
    path.write_text(
        "opened_at,trigger_type,trigger_text,flagged_txn_id,card_id,customer_id,risk_score\n"
        "2016-12-10 15:01:21,customer_report,x,3530164,C08623-K2,C08623,\n",
        encoding="utf-8",
    )
    with pytest.raises(KeyError, match="no alert id column"):
        CasePackLoader(path).load()


# ── TriggerContext ───────────────────────────────────────────────────────────


def test_a_customer_report_is_a_denial_already_in_hand(alerts: list[Alert]) -> None:
    by_id = {a.alert_id: a for a in alerts}
    context = TriggerContext.from_alert(by_id["HHG-003"])
    assert context.customer_already_denied is True
    assert context.model_score_at_alert is None
    assert context.analyst_hint == ""
    assert context.flagged_txn_id == "3530164"


def test_a_risk_score_alert_carries_its_score_and_no_denial(alerts: list[Alert]) -> None:
    by_id = {a.alert_id: a for a in alerts}
    context = TriggerContext.from_alert(by_id["HHG-001"])
    assert context.customer_already_denied is False
    assert context.model_score_at_alert == 0.61
    assert context.analyst_hint == ""


def test_the_one_analyst_request_keeps_its_lead(alerts: list[Alert]) -> None:
    by_id = {a.alert_id: a for a in alerts}
    context = TriggerContext.from_alert(by_id["HHG-014"])
    assert context.trigger_type is TriggerType.ANALYST_REQUEST
    assert "same unusual device profile" in context.analyst_hint
    assert context.customer_already_denied is False
