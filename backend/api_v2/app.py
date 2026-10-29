"""HTTP surface for the Round 2 layer and the regulator's view.

    cd backend && uvicorn api_v2.app:app --port 8100       # then open http://127.0.0.1:8100/regulator

Reads one run directory (``SENTINEL_RUN_DIR``, default eval/out/regulator_demo):
``cases/*.json`` answer files, ``audit/ledger.jsonl`` (the hash-chained trail),
``containment/*.json``. Nothing here decides anything: investigations run the
same code as the CLI (sentinel.typologies.run), approvals go through
sentinel.typologies.approvals, and every write is a ledger entry.

Identity: with ``SENTINEL_AUTH_SECRET`` set, every request needs a server-signed
bearer token (api/auth.py; ``?token=`` also accepted). Reading requires any valid
identity; starting an investigation requires an analyst or above; approving an
L1/L2 action requires a role on that route's approver list. Without the secret
the service refuses all writes and serves reads in demo mode, marked as such.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from api import auth
from sentinel.typologies.audit import Ledger, verify

ROOT = Path(__file__).resolve().parents[2]
RUN = Path(os.getenv("SENTINEL_RUN_DIR", str(ROOT / "eval" / "out" / "regulator_demo")))
PACK = Path(os.getenv("SENTINEL_PACK", str(ROOT / "case_pack.csv")))
STATIC = Path(__file__).with_name("static")

app = FastAPI(title="Sentinel v2 — investigations, approvals, regulator view", version="2.1.0")


def ledger() -> Ledger:
    return Ledger(RUN / "audit" / "ledger.jsonl")


def who(request: Request, *, write: bool = False):
    if not auth.secret():
        if write:
            raise HTTPException(403, "writes are disabled: SENTINEL_AUTH_SECRET is not configured on this server")
        return None
    try:
        return auth.resolve_principal(request.headers, request.query_params, "")
    except auth.AuthError as exc:
        raise HTTPException(401, f"invalid or missing token: {exc}") from exc


def _answers() -> dict[str, dict]:
    out = {}
    for p in sorted((RUN / "cases").glob("*.json")):
        out[p.stem] = json.loads(p.read_text(encoding="utf8"))
    return out


def _summary_row(cid: str, ans: dict | None, entries: list[dict]) -> dict:
    kinds = [e["kind"] for e in entries]
    status = next((e["data"].get("to") for e in reversed(entries) if e["kind"] == "status.changed"), None)
    c = (ans or {}).get("case", {})
    pend = [e["data"]["action"] for e in entries if e["kind"] == "approval.requested"]
    done = {e["data"]["action"] for e in entries if e["kind"] in ("approval.granted", "approval.rejected")}
    return {"case_id": cid, "verdict": c.get("verdict"), "fraud_probability": c.get("fraud_probability"),
            "pattern": c.get("pattern"), "exposure_usd": c.get("exposure_usd"), "status": status or c.get("status"),
            "written_to_graph": c.get("written_to_graph"), "sar_filed": (ans or {}).get("sar", {}).get("file"),
            "steps": len(entries), "queries": kinds.count("step.query"), "pending": [a for a in pend if a not in done],
            "denied_attempts": kinds.count("approval.denied"), "failed": "case.failed" in kinds,
            "opened_at": next((e["data"]["alert"]["opened_at"] for e in entries if e["kind"] == "case.opened"), None)}


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/regulator")


@app.get("/regulator", response_class=HTMLResponse, include_in_schema=False)
def regulator():
    page = (STATIC / "regulator.html").read_text(encoding="utf8")
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body>'
            + page + "</body></html>")


@app.get("/v2/summary")
def summary(request: Request):
    p = who(request)
    led = ledger()
    entries = led.entries()
    answers = _answers()
    ids = sorted({e["case_id"] for e in entries} | set(answers))
    rows = [_summary_row(i, answers.get(i), [e for e in entries if e["case_id"] == i]) for i in ids]
    return {"run_dir": str(RUN.relative_to(ROOT)) if RUN.is_relative_to(ROOT) else str(RUN),
            "chain": verify(led.path), "investigations": rows,
            "viewer": p.as_dict() if p else {"actor_id": "demo", "actor_role": "read-only", "authenticated": False}}


@app.get("/v2/investigations/{case_id}")
def investigation(case_id: str, request: Request):
    who(request)
    ans = _answers().get(case_id)
    entries = ledger().entries(case_id)
    if not ans and not entries:
        raise HTTPException(404, f"no investigation {case_id}")
    cp = RUN / "containment" / f"{case_id}.json"
    return {"answer": ans, "entries": entries,
            "containment": json.loads(cp.read_text(encoding="utf8")) if cp.exists() else None}


@app.get("/v2/ledger", response_class=PlainTextResponse)
def raw_ledger(request: Request):
    """The ledger exactly as stored, so a reader can recompute every hash itself."""
    who(request)
    p = ledger().path
    return p.read_text(encoding="utf8") if p.exists() else ""


@app.get("/v2/verify")
def verify_chain(request: Request):
    who(request)
    return verify(ledger().path)


@app.get("/v2/changes")
def changes(request: Request):
    """Change records: schema-drift approvals, and every status and configuration change."""
    who(request)
    d = ROOT / "data" / "round2"
    rd = lambda n: json.loads((d / n).read_text(encoding="utf8")) if (d / n).exists() else None  # noqa: E731
    entries = ledger().entries()
    return {"schema_drift": {"report": rd("drift_report.json"), "approval": rd("drift_approval.json")},
            "status_changes": [e for e in entries if e["kind"] == "status.changed"],
            "configurations": [{"seq": e["seq"], "case_id": e["case_id"], "at": e["at"],
                                "code_version": e["data"]["code_version"], "rules": e["data"]["rules"],
                                "thresholds": e["data"]["thresholds"], "source": e["data"]["source"],
                                "model_enabled": e["data"]["model_enabled"]}
                               for e in entries if e["kind"] == "case.opened"]}


@app.post("/v2/investigations")
async def start(request: Request):
    p = who(request, write=True)
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
        write_back(ans, row, src, actor=p.id, ledger=led)
    except GraphUnavailable as exc:
        led.append(row["case_id"], "case.failed", {"error": str(exc), "written": False}, actor=p.id, role=p.role.value)
        raise HTTPException(503, f"graph unavailable; nothing written: {exc}") from exc
    (RUN / "cases").mkdir(parents=True, exist_ok=True)
    (RUN / "cases" / f"{row['case_id']}.json").write_text(json.dumps(ans, indent=1, ensure_ascii=False), encoding="utf8")
    if containment:
        (RUN / "containment").mkdir(exist_ok=True)
        (RUN / "containment" / f"{row['case_id']}.json").write_text(json.dumps(containment, indent=1, default=str),
                                                                    encoding="utf8")
    led.append(row["case_id"], "status.changed", {"from": "new", "to": ans["case"]["status"],
                                                  "verdict": ans["case"]["verdict"]}, actor=p.id, role=p.role.value)
    return {"case_id": row["case_id"], "verdict": ans["case"]["verdict"], "status": ans["case"]["status"]}


@app.post("/v2/investigations/{case_id}/approvals")
async def approve(case_id: str, request: Request):
    who(request, write=True)
    body = await request.json()
    token = auth.bearer(request.headers.get("Authorization")) or request.query_params.get("token", "")
    from sentinel.typologies.approvals import ApprovalError, decide

    try:
        e = decide(ledger(), case_id, body["action"], token, approve=bool(body.get("approve", True)),
                   note=str(body.get("note", ""))[:500])
    except ApprovalError as exc:
        raise HTTPException(403, str(exc)) from exc
    return e
