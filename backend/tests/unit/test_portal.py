"""Sign-in and role-based access on the v2 service: each role reaches exactly what the table grants."""

from __future__ import annotations

import importlib
import json
import shutil

import pytest
from fastapi.testclient import TestClient

from api import auth

PASSWORDS = {"raj": ("analyst", "analyst-pass-123"), "priya": ("team_lead", "lead-pass-1234"),
             "anika": ("fraud_manager", "manager-pass-1"), "ines": ("regulator", "examiner-pass-1")}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(auth.SECRET_ENV, "portal-test-secret")
    monkeypatch.setenv("SENTINEL_USERS_FILE", str(tmp_path / "users.json"))
    run = tmp_path / "run"
    (run / "cases").mkdir(parents=True)
    (run / "audit").mkdir()
    answer = {"case_id": "HHG-014", "case": {"verdict": "fraud", "status": "escalated", "fraud_probability": 0.97,
                                            "pattern": "undocumented", "exposure_usd": 187.33, "written_to_graph": True,
                                            "graph_case_id": "CASE-HHG-014", "evidence": []}, "sar": {"file": True}}
    (run / "cases" / "HHG-014.json").write_text(json.dumps(answer))
    monkeypatch.setenv("SENTINEL_RUN_DIR", str(run))
    import api_v2.users as users
    import api_v2.app as app_mod
    importlib.reload(users)
    importlib.reload(app_mod)
    for u, (role, pw) in PASSWORDS.items():
        users.add(u, pw, role)
    led = app_mod.ledger()
    led.append("HHG-014", "case.opened", {"alert": {"opened_at": "2016-11-22 20:11:00"}})
    led.append("HHG-014", "approval.requested", {"action": "BLOCK_CARD", "route": "L1", "reason": "R2",
                                                 "approvers": ["team_lead", "fraud_manager"]})
    led.append("HHG-014", "approval.requested", {"action": "FILE_REPORT", "route": "L2", "reason": "3a",
                                                 "approvers": ["fraud_manager"]})
    return TestClient(app_mod.app), app_mod


def signin(c, user):
    r = c.post("/v2/login", json={"username": user, "password": PASSWORDS[user][1]})
    assert r.status_code == 200, r.text
    return r.json()


def test_wrong_password_is_refused_and_logged(client):
    c, app_mod = client
    assert c.post("/v2/login", json={"username": "raj", "password": "nope-nope-nope"}).status_code == 401
    assert c.post("/v2/login", json={"username": "ghost", "password": "whatever-123"}).status_code == 401
    kinds = [e["kind"] for e in app_mod.ledger().entries("_access")]
    assert kinds.count("login.failed") == 2


def test_five_failures_lock_the_account(client):
    c, _ = client
    for _ in range(5):
        c.post("/v2/login", json={"username": "raj", "password": "wrong-wrong-1"})
    assert c.post("/v2/login", json={"username": "raj", "password": PASSWORDS["raj"][1]}).status_code == 401


def test_session_cookie_is_httponly_and_role_comes_from_it(client):
    c, _ = client
    r = c.post("/v2/login", json={"username": "priya", "password": PASSWORDS["priya"][1]})
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    me = c.get("/v2/me", headers={"X-Sentinel-Role": "fraud_manager"}).json()
    assert me["actor_role"] == "team_lead" and me["authenticated"]


def test_nothing_without_signing_in(client):
    c, _ = client
    for path in ("/v2/me", "/v2/queue", "/v2/summary", "/v2/ledger", "/v2/users"):
        assert c.get(path).status_code == 401, path


@pytest.mark.parametrize("user,path,code", [
    ("raj", "/v2/queue", 200), ("raj", "/v2/summary", 403), ("raj", "/v2/ledger", 403), ("raj", "/v2/users", 403),
    ("priya", "/v2/queue", 200), ("priya", "/v2/users", 403), ("priya", "/v2/changes", 403),
    ("anika", "/v2/users", 200), ("anika", "/v2/summary", 200), ("anika", "/v2/ledger", 200),
    ("ines", "/v2/summary", 200), ("ines", "/v2/ledger", 200), ("ines", "/v2/changes", 200),
    ("ines", "/v2/queue", 403), ("ines", "/v2/users", 403), ("ines", "/v2/investigations/HHG-014", 200),
])
def test_reads_follow_the_table(client, user, path, code):
    c, _ = client
    signin(c, user)
    assert c.get(path).status_code == code


def test_regulator_cannot_change_anything(client):
    c, _ = client
    signin(c, "ines")
    assert c.post("/v2/investigations", json={"case_id": "HHG-014"}).status_code == 403
    assert c.post("/v2/investigations/HHG-014/approvals", json={"action": "BLOCK_CARD"}).status_code == 403


def test_approvals_follow_the_route(client):
    c, app_mod = client
    signin(c, "raj")
    assert c.post("/v2/investigations/HHG-014/approvals", json={"action": "BLOCK_CARD"}).status_code == 403
    signin(c, "priya")
    assert c.post("/v2/investigations/HHG-014/approvals", json={"action": "FILE_REPORT"}).status_code == 403
    assert c.post("/v2/investigations/HHG-014/approvals", json={"action": "BLOCK_CARD", "note": "ok"}).status_code == 200
    signin(c, "anika")
    assert c.post("/v2/investigations/HHG-014/approvals", json={"action": "FILE_REPORT", "approve": False,
                                                                 "note": "add the sibling cards"}).status_code == 200
    kinds = [e["kind"] for e in app_mod.ledger().entries()]
    assert "access.refused" in kinds and kinds.count("login.succeeded") == 3
    from sentinel.typologies.audit import verify
    assert verify(app_mod.ledger().path)["ok"]


def test_queue_marks_what_awaits_you(client):
    c, _ = client
    signin(c, "priya")
    row = next(a for a in c.get("/v2/queue").json()["alerts"] if a["case_id"] == "HHG-014")
    assert row["awaiting_you"] == 1  # BLOCK_CARD (L1); FILE_REPORT is L2 and not hers


def test_pages_carry_the_shared_theme(client):
    c, _ = client
    for page in ("/login", "/portal", "/regulator"):
        html = c.get(page).text
        assert "--accent: #b54a09" in html and "Space+Grotesk" in html and "<!--THEME-->" not in html, page
