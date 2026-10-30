"""The audit trail cannot be edited quietly, and approvals follow the route table."""

from __future__ import annotations

import json

import pytest

from api import auth
from sentinel.typologies.approvals import ApprovalError, decide, pending
from sentinel.typologies.audit import Ledger, verify

KEY = "audit-test-secret"


@pytest.fixture
def led(tmp_path, monkeypatch):
    monkeypatch.setenv(auth.SECRET_ENV, KEY)
    lg = Ledger(tmp_path / "ledger.jsonl")
    lg.append("C-1", "case.opened", {"alert": {"opened_at": "2017-01-02 10:00:00"}})
    lg.append("C-1", "approval.requested", {"action": "BLOCK_CARD", "route": "L1", "reason": "R2",
                                            "approvers": ["team_lead", "fraud_manager"]})
    lg.append("C-1", "approval.requested", {"action": "FILE_REPORT", "route": "L2", "reason": "3a",
                                            "approvers": ["fraud_manager"]})
    return lg


def _rewrite(path, fn):
    lines = path.read_text(encoding="utf8").splitlines()
    path.write_text("\n".join(fn(lines)) + "\n", encoding="utf8")


def test_untouched_chain_verifies(led):
    assert verify(led.path) == {"ok": True, "entries": 3, "first_broken": None}


def test_edit_is_detected_at_the_edited_entry(led):
    def edit(lines):
        e = json.loads(lines[1]); e["data"]["route"] = "auto"; lines[1] = json.dumps(e); return lines
    _rewrite(led.path, edit)
    assert verify(led.path)["first_broken"] == 2


def test_deletion_and_reordering_are_detected(led):
    _rewrite(led.path, lambda ls: [ls[0], ls[2]])
    assert not verify(led.path)["ok"]


def test_a_new_ledger_object_continues_the_same_chain(led):
    Ledger(led.path).append("C-1", "note", {})
    assert verify(led.path)["ok"] and verify(led.path)["entries"] == 4


def test_analyst_cannot_approve_and_the_attempt_is_recorded(led):
    with pytest.raises(ApprovalError):
        decide(led, "C-1", "BLOCK_CARD", auth.mint("raj", "analyst"))
    denied = [e for e in led.entries("C-1") if e["kind"] == "approval.denied"]
    assert denied and denied[0]["actor"] == "raj" and denied[0]["role"] == "analyst"


def test_team_lead_cannot_approve_l2(led):
    with pytest.raises(ApprovalError, match="L2"):
        decide(led, "C-1", "FILE_REPORT", auth.mint("priya", "team_lead"))


def test_full_chain_closes_the_case(led):
    decide(led, "C-1", "BLOCK_CARD", auth.mint("priya", "team_lead"), note="ok")
    assert [p["action"] for p in pending(led, "C-1")] == ["FILE_REPORT"]
    decide(led, "C-1", "FILE_REPORT", auth.mint("anika", "fraud_manager"))
    kinds = [e["kind"] for e in led.entries("C-1")]
    assert kinds[-1] == "status.changed" and led.entries("C-1")[-1]["data"]["to"] == "actions_approved"
    assert verify(led.path)["ok"]


def test_an_action_cannot_be_approved_twice(led):
    decide(led, "C-1", "BLOCK_CARD", auth.mint("priya", "team_lead"))
    with pytest.raises(ApprovalError, match="not awaiting"):
        decide(led, "C-1", "BLOCK_CARD", auth.mint("anika", "fraud_manager"))


def test_forged_token_is_refused_and_recorded(led):
    with pytest.raises(ApprovalError, match="invalid token"):
        decide(led, "C-1", "BLOCK_CARD", "sv1.e30.AAAA")
    assert led.entries("C-1")[-1]["kind"] == "approval.denied"


def test_personal_data_in_free_text_is_masked_but_facts_survive(tmp_path):
    from sentinel.typologies.audit import redact

    lg = Ledger(tmp_path / "l.jsonl")
    e = lg.append("C-2", "case.opened", {"alert": {
        "trigger_text": "Customer jane.doe@mail.com, +1 (555) 201-4477, card 4111 1111 1111 1111, txn 3514030 on 2016-11-22 20:11:00",
        "card_id": "C13487-K1"}})
    t = e["data"]["alert"]["trigger_text"]
    assert "jane.doe" not in t and "201-4477" not in t and "4111 1111" not in t
    assert "3514030" in t and "2016-11-22 20:11:00" in t and e["data"]["alert"]["card_id"] == "C13487-K1"
    assert redact("amount $1,906.07 in region 444.0") == "amount $1,906.07 in region 444.0"
    assert verify(lg.path)["ok"]


@pytest.mark.parametrize("change,needle", [
    ({"trigger_type": "telepathy"}, "trigger_type"), ({"opened_at": "not-a-date"}, "opened_at"),
    ({"flagged_txn_id": "abc"}, "flagged_txn_id"), ({"card_id": "C99999-K1"}, "does not belong"),
    ({"case_id": ""}, "case_id is empty"),
])
def test_malformed_alerts_are_rejected_before_any_query(change, needle):
    from sentinel.typologies.run import InvalidAlert, validate_alert

    row = {"case_id": "X-1", "opened_at": "2017-01-02 10:00:00", "trigger_type": "risk_score", "trigger_text": "",
           "flagged_txn_id": "3660100", "card_id": "C00450-K1", "customer_id": "C00450", "risk_score": "0.1"}
    validate_alert(row)
    with pytest.raises(InvalidAlert, match=needle):
        validate_alert({**row, **change})
