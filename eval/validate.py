"""Validate answer files against the required format and against the graph.

This is the gate that stops a hallucinated ID reaching the submission. Made-up
IDs score zero, so every transaction, card, device profile and closed case named
in an answer file is checked to exist in TigerGraph before the file is accepted.

    python -m eval.validate                # all files in cases/
    python -m eval.validate cases/HHG-003.json
    python -m eval.validate --no-graph     # shape only, no database needed
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from etl import config as cfg  # noqa: E402

ACTIONS = {
    "ALLOW_TRANSACTION", "DECLINE_TRANSACTION", "MONITOR_CARD", "MONITOR_CONNECTED_CARDS",
    "WARN_CUSTOMER", "VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH", "BLOCK_CARD", "BLOCK_ALL_CARDS",
    "GENERATE_REPORT", "CREATE_CASE", "FILE_REPORT", "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD",
}
ROUTES = {"auto", "L1", "L2"}
STATUSES = {"open", "closed_fraud", "closed_legitimate", "escalated"}
VERDICTS = {"fraud", "legitimate", "uncertain"}
PATTERNS = {
    "card_testing", "card_not_present_fraud", "card_not_present_new_device",
    "out_of_region_use", "account_takeover", "undocumented", "none",
}
SOURCES = {"graph", "document", "customer", "external"}
REQUEST_TYPES = {"customer_validation", "step_up_auth", "analyst_info"}

# Routing table from the policy. BLOCK_CARD depends on exposure, so it is checked
# separately rather than listed here.
FIXED_ROUTES = {
    "ALLOW_TRANSACTION": "auto", "MONITOR_CARD": "auto", "MONITOR_CONNECTED_CARDS": "auto",
    "WARN_CUSTOMER": "auto", "VERIFY_WITH_CUSTOMER": "auto", "STEP_UP_AUTH": "auto",
    "GENERATE_REPORT": "auto", "CREATE_CASE": "auto", "ESCALATE_TO_ANALYST": "auto",
    "CLOSE_NO_FRAUD": "auto", "DECLINE_TRANSACTION": "L1",
    "BLOCK_ALL_CARDS": "L2", "FILE_REPORT": "L2",
}


class Report:
    def __init__(self, name: str):
        self.name = name
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors


def check_shape(a: dict, r: Report) -> None:
    for field in ("case_id", "case", "evidence_requests", "next_best_actions", "sar",
                  "stop_reason", "tool_calls", "tokens", "latency_s"):
        if field not in a:
            r.err(f"missing top-level field '{field}'")
    if r.errors:
        return

    c = a["case"]
    for field in ("status", "verdict", "fraud_probability", "pattern", "pattern_description",
                  "affected_txn_ids", "first_suspicious_txn_id", "connected_card_ids",
                  "connected_device_profiles", "exposure_usd", "evidence",
                  "similar_prior_cases", "summary", "written_to_graph", "graph_case_id"):
        if field not in c:
            r.err(f"case: missing field '{field}'")

    if c.get("status") not in STATUSES:
        r.err(f"case.status '{c.get('status')}' not in {sorted(STATUSES)}")
    if c.get("verdict") not in VERDICTS:
        r.err(f"case.verdict '{c.get('verdict')}' not in {sorted(VERDICTS)}")
    if c.get("pattern") not in PATTERNS:
        r.err(f"case.pattern '{c.get('pattern')}' not in {sorted(PATTERNS)}")
    p = c.get("fraud_probability")
    if not isinstance(p, (int, float)) or not 0 <= p <= 1:
        r.err(f"case.fraud_probability {p!r} is not a number in 0..1")
    if c.get("pattern") == "undocumented" and not c.get("pattern_description", "").strip():
        r.err("pattern is 'undocumented' but pattern_description is empty")
    if c.get("pattern") != "undocumented" and c.get("pattern_description", ""):
        r.warn("pattern_description is set but pattern is not 'undocumented'")

    for i, e in enumerate(c.get("evidence", [])):
        for field in ("claim", "source", "ref", "entity_ids"):
            if field not in e:
                r.err(f"evidence[{i}]: missing '{field}'")
        if e.get("source") not in SOURCES:
            r.err(f"evidence[{i}].source '{e.get('source')}' not in {sorted(SOURCES)}")
    if not c.get("evidence"):
        r.err("case.evidence is empty")

    for i, req in enumerate(a.get("evidence_requests", [])):
        if req.get("type") not in REQUEST_TYPES:
            r.err(f"evidence_requests[{i}].type '{req.get('type')}' not in {sorted(REQUEST_TYPES)}")
        for field in ("asked_after_step", "assumed_response"):
            if field not in req:
                r.err(f"evidence_requests[{i}]: missing '{field}'")


def check_actions(a: dict, r: Report) -> None:
    nba = a.get("next_best_actions", {})
    for field in ("initial", "final", "what_changed"):
        if field not in nba:
            r.err(f"next_best_actions: missing '{field}'")
    exposure = a.get("case", {}).get("exposure_usd", 0)

    for phase in ("initial", "final"):
        seen = set()
        for i, act in enumerate(nba.get(phase, [])):
            name, route = act.get("action"), act.get("route")
            if name not in ACTIONS:
                r.err(f"{phase}[{i}]: action '{name}' is not a policy action")
                continue
            if route not in ROUTES:
                r.err(f"{phase}[{i}]: route '{route}' not in {sorted(ROUTES)}")
            if name in FIXED_ROUTES and route != FIXED_ROUTES[name]:
                r.err(f"{phase}[{i}]: {name} must route '{FIXED_ROUTES[name]}', got '{route}'")
            if name == "BLOCK_CARD":
                want = "L1" if exposure <= 2500 else "L2"
                if route != want:
                    r.err(f"{phase}[{i}]: BLOCK_CARD at exposure ${exposure:,.2f} must route '{want}'")
            if not act.get("reason", "").strip():
                r.err(f"{phase}[{i}]: {name} has no reason")
            if name in seen:
                r.warn(f"{phase}: {name} listed twice")
            seen.add(name)

    if not a.get("evidence_requests") and nba.get("initial") != nba.get("final"):
        r.err("no evidence was requested, so 'final' must equal 'initial'")

    # R10: BLOCK_ALL_CARDS needs two compromised cards or confirmed credential theft.
    for phase in ("initial", "final"):
        names = {x.get("action") for x in nba.get(phase, [])}
        if "BLOCK_ALL_CARDS" in names:
            if len(a["case"].get("connected_card_ids", [])) < 1:
                r.err(f"{phase}: R10 bars BLOCK_ALL_CARDS without a second compromised card")


def check_consistency(a: dict, r: Report) -> None:
    c, sar = a["case"], a.get("sar", {})
    final_names = {x.get("action") for x in a.get("next_best_actions", {}).get("final", [])}

    if sar.get("file") != ("FILE_REPORT" in final_names):
        r.err(f"sar.file is {sar.get('file')} but FILE_REPORT "
              f"{'is' if 'FILE_REPORT' in final_names else 'is not'} in final actions")

    if sar.get("file"):
        if not sar.get("narrative", "").strip():
            r.err("sar.file is true but narrative is empty")
        elif len(sar["narrative"].split(".")) < 6:
            r.warn("sar.narrative looks shorter than the required six to twelve sentences")
        if not sar.get("subjects"):
            r.err("sar.file is true but subjects is empty")
        if len(sar.get("activity_dates", [])) != 2:
            r.err("sar.activity_dates must hold exactly two dates")
    else:
        if sar.get("narrative") or sar.get("subjects") or sar.get("total_amount_usd") \
                or sar.get("activity_dates"):
            r.err("sar.file is false, so narrative/subjects/total_amount_usd/activity_dates "
                  "must be empty, empty, 0 and empty")

    if c["verdict"] == "legitimate":
        if c["affected_txn_ids"]:
            r.err("verdict is legitimate, so affected_txn_ids must be empty")
        if c["exposure_usd"] != 0:
            r.err("verdict is legitimate, so exposure_usd must be 0")
        if sar.get("file"):
            r.err("verdict is legitimate, so sar.file must be false")

    if c["first_suspicious_txn_id"] and c["first_suspicious_txn_id"] not in c["affected_txn_ids"]:
        r.warn("first_suspicious_txn_id is not listed in affected_txn_ids")

    if c.get("written_to_graph") and not c.get("graph_case_id"):
        r.err("written_to_graph is true but graph_case_id is empty")


def check_graph(a: dict, r: Report, conn) -> None:
    """Every ID named in the answer must exist in TigerGraph."""
    c = a["case"]
    want: dict[str, set[str]] = {"Transaction": set(), "Card": set(), "ClosedCase": set()}
    want["Transaction"].update(c["affected_txn_ids"])
    if c["first_suspicious_txn_id"]:
        want["Transaction"].add(c["first_suspicious_txn_id"])
    want["Card"].update(c["connected_card_ids"])
    want["ClosedCase"].update(c["similar_prior_cases"])
    for e in c["evidence"]:
        for eid in e.get("entity_ids", []):
            if eid.startswith("CC-"):
                want["ClosedCase"].add(eid)
            elif "-K" in eid:
                want["Card"].add(eid)
            elif eid.isdigit():
                want["Transaction"].add(eid)

    for vtype, ids in want.items():
        for vid in sorted(ids):
            try:
                if not conn.getVerticesById(vtype, vid):
                    r.err(f"{vtype} '{vid}' does not exist in the graph")
            except Exception:  # noqa: BLE001
                r.err(f"{vtype} '{vid}' does not exist in the graph")

    # Exposure must equal the summed absolute amounts of the affected transactions.
    if c["affected_txn_ids"]:
        total = 0.0
        for txn_id in c["affected_txn_ids"]:
            try:
                total += abs(conn.getVerticesById("Transaction", txn_id)[0]["attributes"]["amt"])
            except Exception:  # noqa: BLE001
                return
        if abs(total - c["exposure_usd"]) > 0.02:
            r.err(f"exposure_usd is {c['exposure_usd']:,.2f} but the affected transactions "
                  f"sum to {total:,.2f}")

    if c.get("written_to_graph"):
        try:
            if not conn.getVerticesById("FraudCase", c["graph_case_id"]):
                r.err(f"written_to_graph is true but FraudCase '{c['graph_case_id']}' is not in the graph")
        except Exception:  # noqa: BLE001
            r.err(f"written_to_graph is true but FraudCase '{c['graph_case_id']}' is not in the graph")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="answer files (default: everything in cases/)")
    ap.add_argument("--no-graph", action="store_true", help="skip the database checks")
    args = ap.parse_args()

    paths = [Path(f) for f in args.files] or sorted(cfg.CASES_DIR.glob("HHG-*.json"))
    if not paths:
        raise SystemExit("no answer files found in cases/")

    conn = None if args.no_graph else cfg.connect(verbose=False)

    reports = []
    for path in paths:
        r = Report(path.name)
        try:
            a = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            r.err(f"invalid JSON: {exc}")
            reports.append(r)
            continue
        check_shape(a, r)
        if not r.errors:
            check_actions(a, r)
            check_consistency(a, r)
            if conn:
                check_graph(a, r, conn)
        reports.append(r)

    print(f"validated {len(reports)} file(s)" + ("" if conn else " (shape only)"))
    for r in reports:
        status = "ok  " if r.ok else "FAIL"
        print(f"  [{status}] {r.name}")
        for w in r.warnings:
            print(f"         warn: {w}")
        for e in r.errors:
            print(f"         error: {e}")

    missing = 20 - len(paths)
    if missing > 0:
        print(f"\n  note: {missing} of the 20 required answer files are not written yet")

    sys.exit(0 if all(r.ok for r in reports) else 1)


if __name__ == "__main__":
    main()
