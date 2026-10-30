"""The grader's published checks, re-implemented, run on our own answer files.

The organisers' grade.py was not in the Round 2 folder we received, so this
encodes what the brief says it checks, in its three classes:

  hard     required fields; valid values; internal consistency; every id exists;
           exposure_usd equals the sum of the affected amounts; first_suspicious_txn_id
           is the earliest affected transaction
  policy   approval routes; R1 (no block on a single weak signal without a denial);
           R8 (uncertain and exposed -> escalate); R9 (undocumented -> description,
           case, escalate); R10 (BLOCK_ALL_CARDS only with two confirmed cards or
           compromised credentials); section 3a report rules; no tip-off in anything the
           customer would see
  warning  heuristics, not scored

Run:  python -m eval.self_grade [--cases eval/out/regulator_demo/cases]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sentinel.domain.enums import Action, FIXED_ROUTES, Pattern, Route, Verdict  # noqa: E402

REQUIRED = ["case_id", "case", "evidence_requests", "next_best_actions", "sar", "stop_reason", "tool_calls",
            "tokens", "latency_s"]
CASE_REQ = ["status", "verdict", "fraud_probability", "pattern", "pattern_description", "affected_txn_ids",
            "first_suspicious_txn_id", "connected_card_ids", "connected_device_profiles", "exposure_usd", "evidence",
            "similar_prior_cases", "summary", "written_to_graph", "graph_case_id"]
TIPOFF = re.compile(r"\b(SAR|suspicious activity report|report(ed)? to (the )?(regulator|authorit)|"
                    r"file[ds]? a report|FILE_REPORT|money laundering|FinCEN)\b", re.I)


def grade(a: dict, con) -> list[tuple[str, str]]:
    f: list[tuple[str, str]] = []
    hard = lambda m: f.append(("hard", m))  # noqa: E731
    pol = lambda m: f.append(("policy", m))  # noqa: E731
    warn = lambda m: f.append(("warning", m))  # noqa: E731
    for k in REQUIRED:
        if k not in a:
            hard(f"missing {k}")
    c = a.get("case", {})
    for k in CASE_REQ:
        if k not in c:
            hard(f"case.{k} missing")
    if c.get("verdict") not in {v.value for v in Verdict}:
        hard(f"verdict {c.get('verdict')!r}")
    if c.get("pattern") not in {p.value for p in Pattern}:
        hard(f"pattern {c.get('pattern')!r}")
    p = c.get("fraud_probability")
    if not isinstance(p, (int, float)) or not 0 <= p <= 1:
        hard("fraud_probability outside [0,1]")
    final = a["next_best_actions"]["final"]
    initial = a["next_best_actions"]["initial"]
    for r in initial + final:
        if r["action"] not in {x.value for x in Action}:
            hard(f"unknown action {r['action']}")
    # ids exist, and exposure / first-suspicious arithmetic
    ids = c.get("affected_txn_ids", [])
    if ids:
        rows = con.execute("SELECT txn_id, ts, amt FROM txn WHERE txn_id IN (SELECT unnest(?::BIGINT[]))",
                           [[int(i) for i in ids]]).fetchall()
        if len(rows) != len(set(ids)):
            hard(f"{len(set(ids)) - len(rows)} affected ids do not exist")
        total = round(sum(r[2] for r in rows), 2)
        if abs(total - c["exposure_usd"]) > 0.01:
            hard(f"exposure {c['exposure_usd']} != sum of affected {total}")
        earliest = str(min(rows, key=lambda r: (r[1], r[0]))[0]) if rows else ""
        if c["first_suspicious_txn_id"] != earliest:
            hard(f"first_suspicious {c['first_suspicious_txn_id']} is not the earliest ({earliest})")
    elif c.get("exposure_usd"):
        hard("exposure without affected transactions")
    cards = c.get("connected_card_ids", [])
    if cards:
        n = con.execute("SELECT count(DISTINCT card_id) FROM txn WHERE card_id IN (SELECT unnest(?::VARCHAR[]))",
                        [cards]).fetchone()[0]
        if n != len(set(cards)):
            hard(f"{len(set(cards)) - n} connected cards do not exist")
    for cc in c.get("similar_prior_cases", []):
        if cc.startswith("CC-") and not con.execute("SELECT 1 FROM closed_cases WHERE case_id = ?", [cc]).fetchone():
            hard(f"prior case {cc} does not exist")
    for e in c.get("evidence", []):
        for i in e.get("entity_ids", []):
            if i.isdigit() and not con.execute("SELECT 1 FROM txn WHERE txn_id = ?", [int(i)]).fetchone():
                hard(f"evidence cites txn {i} that does not exist")
    # consistency
    acts = {r["action"] for r in final}
    if a["sar"]["file"] != ("FILE_REPORT" in acts):
        hard("sar.file disagrees with FILE_REPORT in final")
    if c.get("verdict") == "legitimate" and (ids or c.get("exposure_usd") or a["sar"]["file"]):
        hard("legitimate case carries an episode, exposure or report")
    if (c.get("pattern") == "undocumented") != bool(c.get("pattern_description", "").strip()):
        hard("pattern_description and undocumented disagree")
    if not a["evidence_requests"] and initial != final:
        hard("final differs from initial with no evidence request")
    # routes
    for r in initial + final:
        act = Action(r["action"])
        want = FIXED_ROUTES.get(act)
        if act is Action.BLOCK_CARD:
            want = Route.L2 if c.get("exposure_usd", 0) > 2500 else Route.L1
        if want and r["route"] != want.value:
            pol(f"{act.value} routed {r['route']}, should be {want.value}")
    # R1: a block on a score alert needs a denial
    trig = a.get("_trigger")
    denied = any("denied" in (q.get("assumed_response", "") + a["next_best_actions"]["what_changed"]).lower()
                 or "not recognise" in q.get("assumed_response", "") for q in a["evidence_requests"])
    if "BLOCK_CARD" in {r["action"] for r in initial} and trig == "risk_score":
        pol("R1: BLOCK_CARD in initial actions on a score-only alert, before verification")
    if "BLOCK_CARD" in acts and trig != "customer_report" and not denied:
        pol("R1: block without a cardholder denial")
    if c.get("verdict") == "legitimate" and acts & {"BLOCK_CARD", "BLOCK_ALL_CARDS", "FILE_REPORT"}:
        pol("blocks or reports a legitimate customer")
    if c.get("verdict") == "uncertain" and c.get("exposure_usd", 0) > 500 and "ESCALATE_TO_ANALYST" not in acts:
        pol("R8: uncertain and exposed > $500 without escalation")
    if c.get("verdict") == "uncertain" and p is not None and p >= 0.70 and not a["sar"]["file"]:
        warn("uncertain at >= 0.70 (strongly suspected) without a report")
    if c.get("pattern") == "undocumented" and not {"CREATE_CASE", "ESCALATE_TO_ANALYST"} <= acts:
        pol("R9: undocumented pattern without CREATE_CASE and ESCALATE_TO_ANALYST")
    if "BLOCK_ALL_CARDS" in acts:
        pol("R10: BLOCK_ALL_CARDS present - check two confirmed cards / compromised credentials")
    strongly = c.get("verdict") == "fraud" or (p or 0) >= 0.70
    connected = bool(cards) or bool(c.get("connected_device_profiles")) or c.get("pattern") == "undocumented"
    must_file = strongly and (c.get("exposure_usd", 0) > 1000 or connected)
    if must_file and not a["sar"]["file"]:
        pol("3a: report required (fraud and exposure > $1,000 or connected/undocumented) but not filed")
    if a["sar"]["file"] and not must_file:
        pol("3a: report filed without its conditions")
    if (p or 0) >= 0.30 and "CREATE_CASE" not in acts:
        pol("3a: case not opened at probability >= 0.30")
    for q in a["evidence_requests"]:
        if TIPOFF.search(q.get("assumed_response", "")):
            pol("tip-off: an evidence request the customer would see mentions a report")
    if a["sar"]["file"]:
        n = len(re.findall(r"[.!?](\s|$)", a["sar"]["narrative"]))
        if not 6 <= n <= 12:
            hard(f"SAR narrative has {n} sentences (6-12)")
    if a.get("tokens", 0) == 0 and any(e.get("source") == "external" for e in c.get("evidence", [])):
        warn("model evidence present but tokens = 0")
    return f


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(ROOT / "eval" / "out" / "regulator_demo" / "cases"))
    ap.add_argument("--pack", default=str(ROOT / "case_pack.csv"))
    a = ap.parse_args()
    import csv

    trig = {r["case_id"]: r["trigger_type"] for r in csv.DictReader(open(a.pack, encoding="utf8"))}
    con = duckdb.connect(str(ROOT / "data" / "lab.duckdb"), read_only=True)
    tally = Counter()
    for p in sorted(Path(a.cases).glob("*.json")):
        ans = json.loads(p.read_text(encoding="utf8"))
        ans["_trigger"] = trig.get(ans["case_id"])
        fs = grade(ans, con)
        tally.update(k for k, _ in fs)
        flag = "PASS" if not [x for x in fs if x[0] != "warning"] else "FAIL"
        print(f"{ans['case_id']} {flag}" + "".join(f"\n    [{k}] {m}" for k, m in fs))
    print(f"hard: {tally['hard']}  policy: {tally['policy']}  warnings: {tally['warning']}")


if __name__ == "__main__":
    main()
