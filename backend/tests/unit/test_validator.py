"""The answer contract, enforced without a network.

The validator runs twice in the real system: inside the orchestrator before a
file is written, and again in CI where there are no credentials. So every rule
here must be decidable from the answer alone. Exposure arithmetic and id
existence need the graph and live in ``GraphIdentityChecker``.

The strongest single test in this file is the last one: the real, hand-built
``cases/HHG-003.json`` must validate clean.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from sentinel.validation.validator import AnswerValidator

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = REPO_ROOT / "cases" / "HHG-003.json"


@pytest.fixture(scope="module")
def golden() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text())


@pytest.fixture
def answer(golden: dict[str, Any]) -> dict[str, Any]:
    """A fresh mutable copy per test, so a mutation cannot leak sideways."""
    return copy.deepcopy(golden)


def validate(payload: dict[str, Any]):
    return AnswerValidator().validate(payload)


def codes(report) -> set[str]:
    return {finding.code for finding in report.errors}


# ── the golden fixture ───────────────────────────────────────────────────────


def test_the_hand_built_golden_case_validates_clean(answer):
    report = validate(answer)
    assert report.ok, f"HHG-003 should validate: {[f.message for f in report.errors]}"


def test_the_golden_case_is_the_shape_the_brief_specifies(golden):
    assert set(golden) == {
        "case_id",
        "case",
        "evidence_requests",
        "next_best_actions",
        "sar",
        "stop_reason",
        "tool_calls",
        "tokens",
        "latency_s",
    }
    assert len(golden["case"]) == 15
    assert len(golden["sar"]) == 6
    assert len(golden["next_best_actions"]) == 3


# ── shape ────────────────────────────────────────────────────────────────────


def test_a_missing_top_level_field_is_an_error(answer):
    del answer["stop_reason"]
    assert not validate(answer).ok


def test_a_missing_case_field_is_an_error(answer):
    del answer["case"]["exposure_usd"]
    assert not validate(answer).ok


def test_an_action_outside_the_fourteen_is_rejected(answer):
    answer["next_best_actions"]["initial"][0]["action"] = "FREEZE_EVERYTHING"
    assert not validate(answer).ok


def test_a_verdict_outside_the_vocabulary_is_rejected(answer):
    answer["case"]["verdict"] = "probably_fine"
    assert not validate(answer).ok


def test_a_probability_outside_zero_to_one_is_rejected(answer):
    answer["case"]["fraud_probability"] = 1.4
    assert not validate(answer).ok


def test_an_empty_evidence_list_is_rejected(answer):
    answer["case"]["evidence"] = []
    assert not validate(answer).ok


def test_an_evidence_source_outside_the_four_is_rejected(answer):
    answer["case"]["evidence"][0]["source"] = "hunch"
    assert not validate(answer).ok


# ── routing ──────────────────────────────────────────────────────────────────


def test_a_fixed_route_that_disagrees_with_the_table_is_rejected(answer):
    for phase in ("initial", "final"):
        for rec in answer["next_best_actions"][phase]:
            if rec["action"] == "CREATE_CASE":
                rec["route"] = "L2"  # CREATE_CASE is always auto
    assert not validate(answer).ok


def test_block_card_routes_by_exposure(answer):
    answer["case"]["verdict"] = "fraud"
    answer["case"]["exposure_usd"] = 2_600.0
    answer["case"]["affected_txn_ids"] = ["3530164"]
    block = {"action": "BLOCK_CARD", "route": "L1", "reason": "R2: customer denied"}
    answer["next_best_actions"]["initial"] = [block]
    answer["next_best_actions"]["final"] = [block]
    answer["evidence_requests"] = []
    # $2,600 is over the threshold, so L1 is wrong.
    assert not validate(answer).ok

    fixed = copy.deepcopy(answer)
    for phase in ("initial", "final"):
        fixed["next_best_actions"][phase] = [{**block, "route": "L2"}]
    assert "route" not in " ".join(f.code for f in validate(fixed).errors)


def test_an_empty_reason_is_rejected(answer):
    answer["next_best_actions"]["initial"][0]["reason"] = ""
    assert not validate(answer).ok


# ── SAR consistency ──────────────────────────────────────────────────────────


def test_sar_file_must_agree_with_the_final_actions(answer):
    answer["sar"]["file"] = True  # but FILE_REPORT is not in final
    assert not validate(answer).ok


def test_file_report_without_the_sar_flag_is_rejected(answer):
    answer["next_best_actions"]["final"].append(
        {"action": "FILE_REPORT", "route": "L2", "reason": "R2"}
    )
    assert not validate(answer).ok


def test_a_non_filing_sar_must_still_carry_its_reason(answer):
    # Guide.md lists sar.reason as required in BOTH branches: "why file, or why
    # not. Cite the policy rule." v1's validator never looked at it.
    answer["sar"]["reason"] = ""
    assert not validate(answer).ok


def test_a_non_filing_sar_must_be_otherwise_empty(answer):
    answer["sar"]["subjects"] = ["C08623"]
    assert not validate(answer).ok


def test_a_filing_sar_needs_a_narrative_subjects_and_two_dates(answer):
    answer["sar"] = {
        "file": True,
        "reason": "R2: confirmed unauthorised use",
        "narrative": "",
        "subjects": [],
        "total_amount_usd": 0,
        "activity_dates": [],
    }
    answer["next_best_actions"]["final"].append(
        {"action": "FILE_REPORT", "route": "L2", "reason": "R2"}
    )
    assert not validate(answer).ok


# ── legitimate cases ─────────────────────────────────────────────────────────


def test_a_legitimate_verdict_must_be_clean(answer):
    answer["case"]["verdict"] = "legitimate"
    answer["case"]["affected_txn_ids"] = ["3530164"]
    answer["case"]["exposure_usd"] = 49.0
    assert not validate(answer).ok


def test_a_legitimate_verdict_with_nothing_affected_is_fine(answer):
    answer["case"]["verdict"] = "legitimate"
    answer["case"]["affected_txn_ids"] = []
    answer["case"]["first_suspicious_txn_id"] = ""
    answer["case"]["exposure_usd"] = 0
    answer["sar"]["file"] = False
    report = validate(answer)
    assert not any("legitimate" in f.code for f in report.errors)


# ── evidence requests ────────────────────────────────────────────────────────


def test_final_must_equal_initial_when_nothing_was_requested(answer):
    answer["evidence_requests"] = []
    answer["next_best_actions"]["final"] = [
        {"action": "MONITOR_CARD", "route": "auto", "reason": "changed without asking"}
    ]
    assert not validate(answer).ok


def test_an_evidence_request_type_outside_the_three_is_rejected(answer):
    if not answer["evidence_requests"]:
        pytest.skip("golden case asked for nothing")
    answer["evidence_requests"][0]["type"] = "telepathy"
    assert not validate(answer).ok


# ── pattern ──────────────────────────────────────────────────────────────────


def test_an_undocumented_pattern_needs_its_description(answer):
    answer["case"]["pattern"] = "undocumented"
    answer["case"]["pattern_description"] = ""
    assert not validate(answer).ok


def test_a_known_pattern_with_a_description_is_at_most_a_warning(answer):
    answer["case"]["pattern"] = "card_testing"
    answer["case"]["pattern_description"] = "left over from an earlier draft"
    report = validate(answer)
    assert not any("pattern_description" in f.path for f in report.errors)


# ── the validator itself ─────────────────────────────────────────────────────


def test_validation_needs_no_network(answer):
    # No repository is injected anywhere in this module. If that ever changes,
    # the orchestrator can no longer validate before writing and CI needs secrets.
    assert validate(answer) is not None


def test_a_report_separates_errors_from_warnings(answer):
    report = validate(answer)
    assert hasattr(report, "errors")
    assert hasattr(report, "warnings")
    assert report.ok is (not report.errors)
