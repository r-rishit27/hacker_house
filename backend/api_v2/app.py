"""HTTP surface for the Round 2 layer: the investigator portal, the approval
chain, and the regulator's view.

    cd backend && uvicorn api_v2.app:app --port 8100
    # http://127.0.0.1:8100/login

Sign-in is username and password (api_v2/users.py), giving a server-signed
session in an HttpOnly cookie; a bearer token (api/auth.py) is also accepted for
scripts. Every endpoint checks one permission from api_v2/rbac.py before it does
anything, so the portal hiding a button is a convenience, never the control.
Sign-ins, failures, lock-outs and refused requests are written to the audit
ledger under the case id ``_access``.

Reads one run directory (``SENTINEL_RUN_DIR``, default eval/out/regulator_demo):
``cases/*.json``, ``audit/ledger.jsonl``, ``containment/*.json``. Without
``SENTINEL_AUTH_SECRET`` the service cannot sign sessions: sign-in and writes are
refused, and reads run in a demo mode marked as such (the snapshot exporter
uses it).
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from api import auth
from api_v2 import rbac, users
from sentinel.domain.enums import Role
from sentinel.typologies.audit import Ledger, verify

ROOT = Path(__file__).resolve().parents[2]
# The signing secret and model keys live in .env; a value already in the environment wins.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
RUN = Path(os.getenv("SENTINEL_RUN_DIR", str(ROOT / "eval" / "out" / "regulator_demo")))
PACK = Path(os.getenv("SENTINEL_PACK", str(ROOT / "case_pack.csv")))
STATIC = Path(__file__).with_name("static")
COOKIE = "sentinel_session"
SESSION_HOURS = float(os.getenv("SENTINEL_SESSION_HOURS", "8"))
ACCESS = "_access"

app = FastAPI(title="Sentinel v2 — investigator portal, approvals, regulator view", version="2.2.0")


def ledger() -> Ledger:
    return Ledger(RUN / "audit" / "ledger.jsonl")


class Viewer:
    """The person behind a request: a verified identity, or the demo reader."""

    def __init__(self, principal=None) -> None:
        self.p = principal

    @property
    def role(self) -> Role | None:
        return self.p.role if self.p else None

    @property
    def id(self) -> str:
        return self.p.id if self.p else "demo"

    def as_dict(self) -> dict:
        if not self.p:
            return {"actor_id": "demo", "actor_role": "read-only", "authenticated": False, "permissions": [],
                    "home": "/regulator"}
        return {**self.p.as_dict(), "home": rbac.HOME[self.p.role],
                "permissions": sorted(rbac.GRANTS[self.p.role]), "capabilities": rbac.describe(self.p.role)}


def viewer(request: Request) -> Viewer:
    if not auth.secret():
        return Viewer(None)
    token = request.cookies.get(COOKIE) or auth.bearer(request.headers.get("Authorization")) \
        or request.query_params.get("token")
    if not token:
        raise HTTPException(401, "sign in first")
    try:
        c = auth.verify(token)
    except auth.AuthError as exc:
        raise HTTPException(401, f"session is not valid: {exc}; sign in again") from exc
    from api.principal import Principal

    return Viewer(Principal(role=c.role, actor_id=c.sub, authenticated=True))


#: Demo mode (no signing secret) may only read what the regulator snapshot shows.
DEMO_READS = {"case.view", "case.audit", "audit.all", "changes.view", "regulator.view"}


def guard(request: Request, permission: str) -> Viewer:
    v = viewer(request)
    if v.p is None:
        if permission in DEMO_READS:
            return v
        raise HTTPException(403, "this server has no signing secret configured: sign-in and changes are disabled")
    try:
        rbac.require(v.role, permission)
    except rbac.Forbidden as exc:
        ledger().append(ACCESS, "access.refused", {"permission": permission, "path": request.url.path},
                        actor=v.id, role=v.role.value)
        raise HTTPException(403, str(exc)) from exc
    return v


FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" '
         'crossorigin><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700'
         '&family=JetBrains+Mono:wght@400;500&display=swap">')


def themed(name: str) -> str:
    """A page with the shared theme inlined where it says <!--THEME-->: one stylesheet, every page."""
    return (STATIC / name).read_text(encoding="utf8").replace(
        "<!--THEME-->", FONTS + "<style>" + (STATIC / "theme.css").read_text(encoding="utf8") + "</style>", 1).replace(
        "<!--NAV-->", "<script>" + (STATIC / "nav.js").read_text(encoding="utf8") + "</script>", 1)


def _page(name: str) -> str:
    body = themed(name)
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body>'
            + body + "</body></html>")


def _answers() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text(encoding="utf8")) for p in sorted((RUN / "cases").glob("*.json"))}


def _summary_row(cid: str, ans: dict | None, entries: list[dict]) -> dict:
    kinds = [e["kind"] for e in entries]
    status = next((e["data"].get("to") for e in reversed(entries) if e["kind"] == "status.changed"), None)
    c = (ans or {}).get("case", {})
    asked = [e["data"] for e in entries if e["kind"] == "approval.requested"]
    done = {e["data"]["action"] for e in entries if e["kind"] in ("approval.granted", "approval.rejected")}
    return {"case_id": cid, "verdict": c.get("verdict"), "fraud_probability": c.get("fraud_probability"),
            "pattern": c.get("pattern"), "exposure_usd": c.get("exposure_usd"), "status": status or c.get("status"),
            "written_to_graph": c.get("written_to_graph"), "sar_filed": (ans or {}).get("sar", {}).get("file"),
            "steps": len(entries), "queries": kinds.count("step.query"),
            "pending": [a["action"] for a in asked if a["action"] not in done],
            "pending_routes": [a["route"] for a in asked if a["action"] not in done],
            "denied_attempts": kinds.count("approval.denied"), "failed": "case.failed" in kinds,
            "opened_at": next((e["data"]["alert"]["opened_at"] for e in entries if e["kind"] == "case.opened"), None)}


# ── pages ────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/login")


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page():
    return _page("login.html")


@app.get("/portal", response_class=HTMLResponse, include_in_schema=False)
def portal_page():
    return _page("portal.html")


@app.get("/regulator", response_class=HTMLResponse, include_in_schema=False)
def regulator_page():
    return _page("regulator.html")


# ── identity ─────────────────────────────────────────────────────────────────

@app.post("/v2/login")
async def login(request: Request):
    if not auth.secret():
        raise HTTPException(503, "sign-in is disabled: SENTINEL_AUTH_SECRET is not configured on this server")
    body = await request.json()
    username = str(body.get("username", ""))[:64]
    try:
        u = users.authenticate(username, str(body.get("password", "")))
    except users.LoginError as exc:
        ledger().append(ACCESS, "login.failed", {"username": username.lower(), "why": str(exc)},
                        actor=username.lower() or "?", role="unauthenticated")
        raise HTTPException(401, "username or password is wrong, or the account is locked") from exc
    token = auth.mint(u["username"], u["role"], hours=SESSION_HOURS)
    ledger().append(ACCESS, "login.succeeded", {"username": u["username"], "session_hours": SESSION_HOURS},
                    actor=u["username"], role=u["role"])
    resp = JSONResponse({"username": u["username"], "name": u["name"], "role": u["role"],
                         "home": rbac.HOME[Role(u["role"])]})
    resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=int(SESSION_HOURS * 3600),
                    secure=request.url.scheme == "https")
    return resp


@app.post("/v2/logout")
def logout(request: Request):
    try:
        v = viewer(request)
        if v.p:
            ledger().append(ACCESS, "logout", {}, actor=v.id, role=v.role.value)
    except HTTPException:
        pass
    resp = JSONResponse({"signed_out": True})
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/v2/me")
def me(request: Request):
    return viewer(request).as_dict()


@app.get("/v2/users")
def list_users(request: Request):
    guard(request, "users.view")
    return {"users": users.all_users(), "roles": {r.value: sorted(rbac.GRANTS[r]) for r in Role},
            "permissions": rbac.PERMISSIONS}


# ── investigations ───────────────────────────────────────────────────────────

@app.get("/v2/queue")
def queue(request: Request):
    """The alert queue: every alert in the pack, and how far each investigation has got."""
    v = guard(request, "queue.view")
    led = ledger().entries()
    answers = _answers()
    out = []
    for r in csv.DictReader(open(PACK, encoding="utf8")):
        cid = r["case_id"]
        s = _summary_row(cid, answers.get(cid), [e for e in led if e["case_id"] == cid]) if cid in answers or \
            any(e["case_id"] == cid for e in led) else None
        mine = [rt for rt in (s or {}).get("pending_routes", []) if v.role and rbac.can(v.role, f"approve.{rt}")]
        out.append({"case_id": cid, "opened_at": r["opened_at"], "trigger_type": r["trigger_type"],
                    "card_id": r["card_id"], "risk_score": r["risk_score"], "investigation": s,
                    "awaiting_you": len(mine)})
    return {"viewer": v.as_dict(), "alerts": out}


@app.get("/v2/summary")
def summary(request: Request):
    v = guard(request, "regulator.view")
    led = ledger()
    entries = [e for e in led.entries() if e["case_id"] != ACCESS]
    answers = _answers()
    ids = sorted({e["case_id"] for e in entries} | set(answers))
    rows = [_summary_row(i, answers.get(i), [e for e in entries if e["case_id"] == i]) for i in ids]
    return {"run_dir": str(RUN.relative_to(ROOT)) if RUN.is_relative_to(ROOT) else str(RUN),
            "chain": verify(led.path), "investigations": rows, "viewer": v.as_dict()}


@app.get("/v2/investigations/{case_id}")
def investigation(case_id: str, request: Request):
    v = guard(request, "case.view")
    ans = _answers().get(case_id)
    entries = ledger().entries(case_id) if case_id != ACCESS else []
    if not ans and not entries:
        raise HTTPException(404, f"no investigation {case_id}")
    cp = RUN / "containment" / f"{case_id}.json"
    may_audit = v.p is None or rbac.can(v.role, "case.audit")
    return {"answer": ans, "entries": entries if may_audit else [],
            "containment": json.loads(cp.read_text(encoding="utf8")) if cp.exists() else None,
            "viewer": v.as_dict()}


@app.post("/v2/investigations")
async def start(request: Request):
    v = guard(request, "case.investigate")
    body = await request.json()
    row = next((r for r in csv.DictReader(open(PACK, encoding="utf8")) if r["case_id"] == body.get("case_id")), None)
    if row is None:
        raise HTTPException(404, f"{body.get('case_id')} is not in {PACK.name}")
    from sentinel.typologies.run import run_case, write_back
    from sentinel.typologies.tg import GraphUnavailable, TigerGraphTxnSource

    led = ledger()
    try:
        src = TigerGraphTxnSource()
        ans = run_case(row, src, use_model=True, ledger=led)
        containment = ans.pop("_containment", None)
        write_back(ans, row, src, actor=v.id, ledger=led)
    except GraphUnavailable as exc:
        led.append(row["case_id"], "case.failed", {"error": str(exc), "written": False}, actor=v.id, role=v.role.value)
        raise HTTPException(503, f"the graph did not answer, so nothing was written: {exc}") from exc
    (RUN / "cases").mkdir(parents=True, exist_ok=True)
    (RUN / "cases" / f"{row['case_id']}.json").write_text(json.dumps(ans, indent=1, ensure_ascii=False), encoding="utf8")
    if containment:
        (RUN / "containment").mkdir(exist_ok=True)
        (RUN / "containment" / f"{row['case_id']}.json").write_text(json.dumps(containment, indent=1, default=str),
                                                                    encoding="utf8")
    led.append(row["case_id"], "status.changed", {"from": "new", "to": ans["case"]["status"],
                                                  "verdict": ans["case"]["verdict"]}, actor=v.id, role=v.role.value)
    return {"case_id": row["case_id"], "verdict": ans["case"]["verdict"], "status": ans["case"]["status"]}


@app.post("/v2/investigations/{case_id}/approvals")
async def approve(case_id: str, request: Request):
    body = await request.json()
    from sentinel.typologies.approvals import ApprovalError, decide, pending

    req = next((p for p in pending(ledger(), case_id) if p["action"] == body.get("action")), None)
    if req is None:
        raise HTTPException(409, f"{body.get('action')} on {case_id} is not awaiting approval")
    guard(request, f"approve.{req['route']}")
    token = request.cookies.get(COOKIE) or auth.bearer(request.headers.get("Authorization")) \
        or request.query_params.get("token", "")
    try:
        e = decide(ledger(), case_id, body["action"], token, approve=bool(body.get("approve", True)),
                   note=str(body.get("note", ""))[:500])
    except ApprovalError as exc:
        raise HTTPException(403, str(exc)) from exc
    return e


# ── audit and change record ──────────────────────────────────────────────────

@app.get("/v2/ledger", response_class=PlainTextResponse)
def raw_ledger(request: Request):
    """The ledger exactly as stored, so a reader can recompute every hash itself."""
    guard(request, "audit.all")
    p = ledger().path
    return p.read_text(encoding="utf8") if p.exists() else ""


@app.get("/v2/verify")
def verify_chain(request: Request):
    guard(request, "audit.all")
    return verify(ledger().path)


@app.get("/v2/access")
def access_log(request: Request):
    """Sign-ins, failures, lock-outs, refused requests."""
    guard(request, "audit.all")
    return {"entries": ledger().entries(ACCESS)}


@app.get("/v2/changes")
def changes(request: Request):
    guard(request, "changes.view")
    d = ROOT / "data" / "round2"
    rd = lambda n: json.loads((d / n).read_text(encoding="utf8")) if (d / n).exists() else None  # noqa: E731
    entries = ledger().entries()
    return {"schema_drift": {"report": rd("drift_report.json"), "approval": rd("drift_approval.json")},
            "status_changes": [e for e in entries if e["kind"] == "status.changed"],
            # .get: entries written by older code, or by hand, may lack a field; show what is there.
            "configurations": [{"seq": e["seq"], "case_id": e["case_id"], "at": e["at"],
                                **{k: e["data"].get(k) for k in ("code_version", "rules", "thresholds", "source",
                                                                 "model_enabled")}}
                               for e in entries if e["kind"] == "case.opened"]}
