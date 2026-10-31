"""Onboarding a new bank: schema parsing, mapping validation, the generic adapter, capabilities, approval.

The end-to-end tests are the point: three banks with three unrelated schemas
(ISO 20022, BIAN, Temenos T24), one reviewed mapping each, and the unchanged
detectors find the same structuring burst — and, where the bank records
devices, the same device ring — in all of them.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from sentinel.onboarding import adapter as A, capabilities as C, fixtures as F, mapping as M, review as R, schema as S
from sentinel.typologies.detectors import DETECTORS

ROOT = Path(__file__).resolve().parents[3]
BANKS = ROOT / "onboarding" / "banks"


def bank(name):
    d = BANKS / name
    return S.load(d / "schema.gsql"), M.load(d / "mapping.json")


# ── schema ───────────────────────────────────────────────────────────────────

def test_parses_multi_pair_edges_reverse_names_and_int_ids():
    s = S.load(BANKS / "Bank_BIAN" / "schema.gsql")
    assert s.edges["planCoversFacility"].pairs == (("CustomerRelationshipManagementPlan", "CurrentAccountFacility"),
                                                   ("CustomerRelationshipManagementPlan", "SavingsAccountFacility"))
    assert s.step("CurrentAccountFacility", "reverse_executionDebitsFacility") == ["PaymentExecutionProcedure"]
    assert S.load(BANKS / "Bank_CoreT24" / "schema.gsql").vertices["CUSTOMER"].primary_id.type == "INT"


def test_a_schema_that_names_an_undeclared_vertex_is_refused():
    with pytest.raises(ValueError, match="undeclared"):
        S.parse("CREATE GRAPH G ()\nADD VERTEX A (PRIMARY_ID id STRING);\nADD DIRECTED EDGE e (FROM A, TO B);")


# ── validation: nothing that is not in the schema gets through ───────────────

def test_validator_refuses_invented_names_and_broken_paths():
    s, good = bank("Bank_CoreT24")
    assert M.validate(s, good) == []
    bad = json.loads(json.dumps(good))
    bad["concepts"]["Transaction"]["sources"].append({"vertex": "PAYMENT"})
    bad["attributes"]["Transaction.amount"]["sources"][0]["attribute"] = "AMT"
    bad["relations"]["INITIATED"]["paths"][0]["steps"] = ["FT_CREDIT_ACCT_NO"]
    problems = " | ".join(M.validate(s, bad))
    assert "unknown vertex 'PAYMENT'" in problems
    assert "no attribute 'AMT'" in problems
    assert "reach" in problems or "unknown edge" in problems


# ── values ───────────────────────────────────────────────────────────────────

def test_core_banking_string_dates_become_datetimes():
    assert A.convert("2610011405", "yymmddhhmm") == datetime(2026, 10, 1, 14, 5)
    assert A.convert("20240310", "yyyymmdd") == datetime(2024, 3, 10)
    assert A.convert("not a date", "yyyymmdd") is None


# ── capabilities: a missing input switches a detector off, by name ───────────

def test_payments_bank_without_devices_has_device_detectors_off_with_reason():
    _, m = bank("Bank_ISO20022")
    r = C.report(m)
    assert not r["detectors"]["shared_device_ring"]["runs"]
    assert "Device" in r["detectors"]["shared_device_ring"]["missing"]
    assert r["detectors"]["structuring_under_threshold"]["runs"]


# ── end to end: same detectors, three schemas ────────────────────────────────

@pytest.mark.parametrize("name", ["Bank_ISO20022", "Bank_BIAN", "Bank_CoreT24"])
def test_structuring_is_found_in_every_bank_through_its_mapping(name):
    s, m = bank(name)
    src = A.MappedTxnSource(s, m, A.InMemoryGraph(s, F.BANKS[name]()))
    acct = F.ACCOUNT0[name]
    txns = src.card_txns(acct, F.AS_OF)
    # 17 ordinary payments, the 3-payment burst and the ring payment: no ledger posting, payment order,
    # batch or return counted as a transaction.
    assert len(txns) == 17 + 3 + 1
    hit = DETECTORS["structuring_under_threshold"](acct, txns, src, F.AS_OF)
    assert hit is not None, f"{name}: structuring not found"
    assert len(hit.txn_ids) == 3 and len(hit.connected_card_ids) == 2


def test_device_ring_found_where_the_bank_records_devices():
    s, m = bank("Bank_BIAN")
    assert "shared_device_ring" in C.enabled(m)
    src = A.MappedTxnSource(s, m, A.InMemoryGraph(s, F.bian()))
    txns = src.card_txns("CAF-0", F.AS_OF)
    hit = DETECTORS["shared_device_ring"]("CAF-0", txns, src, F.AS_OF)
    assert hit is not None and hit.shared == "DEV-TOR"
    assert set(hit.connected_card_ids) == {"CAF-3", "CAF-4", "CAF-5"}


def test_innocent_account_is_quiet_in_every_bank():
    for name in F.BANKS:
        s, m = bank(name)
        src = A.MappedTxnSource(s, m, A.InMemoryGraph(s, F.BANKS[name]()))
        acct = {"Bank_ISO20022": "A4", "Bank_BIAN": "CAF-4", "Bank_CoreT24": "100100004"}[name]
        txns = src.card_txns(acct, F.AS_OF)
        assert DETECTORS["structuring_under_threshold"](acct, txns, src, F.AS_OF) is None


# ── approval: an unapproved or edited mapping is refused ─────────────────────

def test_unapproved_or_edited_mapping_is_refused(tmp_path, monkeypatch):
    from api import auth

    monkeypatch.setenv(auth.SECRET_ENV, "onboarding-test-secret")
    d = tmp_path / "Bank_X"
    d.mkdir()
    (d / "mapping.json").write_text(json.dumps({"bank": "Bank_X", "concepts": {}}), encoding="utf-8")
    with pytest.raises(PermissionError, match="not approved"):
        R.load_for_use(d)
    with pytest.raises(PermissionError, match="refused"):
        R.approve(d, auth.mint("raj", "analyst"))
    R.approve(d, auth.mint("priya", "team_lead"))
    assert R.load_for_use(d)["bank"] == "Bank_X"
    (d / "mapping.json").write_text(json.dumps({"bank": "Bank_X", "concepts": {"Person": {}}}), encoding="utf-8")
    with pytest.raises(PermissionError, match="changed after it was approved"):
        R.load_for_use(d)


def test_runner_accepts_a_banks_own_ids_only_in_bank_mode():
    from sentinel.typologies.run import InvalidAlert, validate_alert

    row = {"case_id": "T24-001", "opened_at": "2026-10-01 09:00:00", "trigger_type": "risk_score",
           "flagged_txn_id": "FT26000104", "card_id": "100100000", "customer_id": "100100"}
    validate_alert(row, bank_ids=True)
    with pytest.raises(InvalidAlert, match="not a transaction id"):
        validate_alert(row)


def test_full_investigation_through_a_mapping_produces_a_valid_answer():
    from sentinel.typologies.run import run_case

    s, m = bank("Bank_CoreT24")
    src = A.MappedTxnSource(s, m, A.InMemoryGraph(s, F.t24()))
    row = {"case_id": "T24-001", "opened_at": "2026-10-01 09:00:00", "trigger_type": "risk_score", "trigger_text": "",
           "flagged_txn_id": "FT26000104", "card_id": "100100000", "customer_id": "100100", "risk_score": "0.71"}
    ans = run_case(row, src, use_model=False)
    assert ans["case"]["verdict"] == "fraud" and ans["case"]["pattern"] == "undocumented"
    assert ans["case"]["affected_txn_ids"] == ["FT26000102", "FT26000103", "FT26000104"]
    assert set(ans["case"]["connected_card_ids"]) == {"100100001", "100100002"}
