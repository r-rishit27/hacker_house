"""Answer a case pack with the Round 2 decision layer.

    python -m sentinel.typologies.run --pack case_pack_final.csv --out out/cases
    python -m sentinel.typologies.run --pack case_pack_final.csv --case HHF-007 --out out/cases
    python -m sentinel.typologies.run ... --no-model      # deterministic only; tricky cases fail closed

Reads TigerGraph GRAPH_GOA by default (``--source lab`` for the DuckDB mirror,
``python scripts/stage_january.py``).
Fails closed: a case that cannot be investigated is reported and *not* written,
rather than written with an invented answer.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from sentinel.typologies.decide import decide
from sentinel.typologies.duck import DuckTxnSource
from sentinel.typologies.offline import build_answer

ROOT = Path(__file__).resolve().parents[3]


def prior_cases(src, card: str, before: datetime) -> list[dict]:
    return src.prior_cases(card, before)


RUN_LOG_FIELDS = ("case_id", "started_at", "source", "latency_s", "graph_queries", "model_calls", "model_tokens",
                  "embedding_tokens", "model", "decided_by", "verdict", "fraud_probability")


class InvalidAlert(ValueError):
    """The alert row itself is malformed; nothing is investigated."""


def validate_alert(row: dict) -> None:
    """Check an alert before any query runs, and say exactly what is wrong."""
    from sentinel.domain.enums import TriggerType

    problems = []
    for k in ("case_id", "opened_at", "trigger_type", "flagged_txn_id", "card_id", "customer_id"):
        if not str(row.get(k) or "").strip():
            problems.append(f"{k} is empty")
    try:
        datetime.fromisoformat(str(row.get("opened_at")))
    except ValueError:
        problems.append(f"opened_at {row.get('opened_at')!r} is not a timestamp")
    if row.get("trigger_type") not in {t.value for t in TriggerType}:
        problems.append(f"trigger_type {row.get('trigger_type')!r} is not one of {[t.value for t in TriggerType]}")
    if not str(row.get("flagged_txn_id", "")).isdigit():
        problems.append(f"flagged_txn_id {row.get('flagged_txn_id')!r} is not a transaction id")
    card, cust = str(row.get("card_id", "")), str(row.get("customer_id", ""))
    if card and cust and not card.startswith(cust + "-K"):
        problems.append(f"card {card} does not belong to customer {cust}")
    if len(str(row.get("trigger_text", ""))) > 5000:
        problems.append("trigger_text is longer than 5,000 characters")
    if problems:
        raise InvalidAlert("; ".join(problems))


def run_case(row: dict, src: DuckTxnSource, *, use_model: bool, log: list | None = None, ledger=None) -> dict:
    from sentinel.typologies import decide as D
    from sentinel.typologies.audit import QueryRecorder, code_version, digest

    validate_alert(row)
    t0, calls0 = time.perf_counter(), src.calls
    started = datetime.now().isoformat(timespec="seconds")
    as_of = datetime.fromisoformat(row["opened_at"])
    cid = row["case_id"]
    if ledger is not None:
        ledger.append(cid, "case.opened", {
            "alert": {k: row.get(k) for k in ("opened_at", "trigger_type", "trigger_text", "flagged_txn_id",
                                              "card_id", "customer_id", "risk_score")},
            "as_of": row["opened_at"], "source": getattr(src, "label", "?"), "code_version": code_version(),
            "model_enabled": use_model, "rules": dict(D.RULES),
            "thresholds": {"model_fraud_floor": D.MODEL_FRAUD_FLOOR, "very_low": D.VERY_LOW,
                           "uncertain_cap": 0.69}})
        src.recorder = QueryRecorder(ledger, cid)
    priors = prior_cases(src, row["card_id"], as_of)
    d = decide(case_id=row["case_id"], trigger_type=row["trigger_type"], trigger_text=row.get("trigger_text", ""),
               card_id=row["card_id"], flagged_txn_id=int(row["flagged_txn_id"]), as_of=as_of, src=src,
               prior_cases=priors, use_model=use_model)
    ans = build_answer(d, case_id=row["case_id"], trigger=row["trigger_type"], card_id=row["card_id"],
                       customer_id=row["customer_id"], flagged_txn_id=int(row["flagged_txn_id"]), as_of=as_of,
                       src=src, prior_cases=priors, tool_calls=src.calls - calls0,
                       latency_s=time.perf_counter() - t0)
    ring = next((h for h in d.hits if h.coordinated), None)
    if ring is not None and d.verdict == "fraud":
        from sentinel.typologies.containment import plan

        ans["_containment"] = plan(ring, row["card_id"], src, as_of)
    if ledger is not None:
        src.recorder = None
        for h in d.hits:
            ledger.append(cid, "step.detector", {"detector": h.key, "pattern": h.pattern, "strength": h.strength,
                                                 "txn_ids": list(h.txn_ids), "connected_cards": len(h.connected_card_ids),
                                                 "shared": h.shared, "description": h.description})
        ledger.append(cid, "step.history_check", {"unexplained": d.unexplained, "explained_by_history": d.explanations,
                                                  "injection_spans": d.injection_spans})
        a = d.adjudication
        if a is not None:
            ledger.append(cid, "step.model", {"model": a.model, "endpoint": "POST /v1/decisions",
                                              "probabilities": a.raw.get("probabilities"), "verdict_from_scores": a.verdict,
                                              "accepted": a.accepted, "rejected_because": a.rejected,
                                              "tokens": a.tokens, "latency_s": round(a.latency_s, 2)})
        ledger.append(cid, "decision", {"verdict": d.verdict, "fraud_probability": d.fraud_probability,
                                        "pattern": d.pattern, "decided_by": d.decided_by, "reasons": d.reasons,
                                        "affected_txn_ids": d.affected_txn_ids})
        nba = ans["next_best_actions"]
        ledger.append(cid, "policy.initial", {"actions": nba["initial"]})
        for r in ans["evidence_requests"]:
            ledger.append(cid, "evidence.requested", r)
        ledger.append(cid, "policy.final", {"actions": nba["final"], "what_changed": nba["what_changed"],
                                            "sar": {"file": ans["sar"]["file"], "reason": ans["sar"]["reason"]}})
        if "_containment" in ans:
            c = ans["_containment"]
            ledger.append(cid, "containment.planned", {k: c[k] for k in ("ring", "shared", "n_blocked", "n_monitored",
                                                                          "exposure_cut", "coverage", "customers_disrupted")})
        for r in nba["final"]:
            if r["route"] in ("L1", "L2"):
                from sentinel.domain.enums import APPROVERS, Route

                ledger.append(cid, "approval.requested", {"action": r["action"], "route": r["route"], "reason": r["reason"],
                                                          "approvers": [x.value for x in APPROVERS[Route(r["route"])]]})
        ledger.append(cid, "answer.produced", {"status": ans["case"]["status"], "answer_digest": digest(ans),
                                               "latency_s": ans["latency_s"], "graph_queries": ans["tool_calls"],
                                               "model_tokens": ans["tokens"]})
    if log is not None:
        a = d.adjudication
        # tokens = the decision model's tokens; it is the only model this pipeline calls.
        # tool_calls = graph (or mirror) queries. latency = wall time, graph + model + policy.
        log.append({"case_id": row["case_id"], "started_at": started, "source": getattr(src, "label", "?"),
                    "latency_s": ans["latency_s"], "graph_queries": ans["tool_calls"],
                    "model_calls": int(a is not None), "model_tokens": a.tokens if a else 0,
                    "embedding_tokens": d.embed_tokens,
                    "model": a.model if a else "", "decided_by": d.decided_by, "verdict": d.verdict,
                    "fraud_probability": d.fraud_probability})
    return ans


def write_back(ans: dict, row: dict, src, *, actor: str, ledger=None) -> None:
    """Write the case into GRAPH_GOA; mark it written only after a matching read-back."""
    from sentinel.domain.answer import AnswerFile
    from sentinel.typologies.writeback import write_case

    c = ans["case"]
    res = write_case(src.conn, ans, row, created_by=actor, cites=c["similar_prior_cases"],
                     devices=c["connected_device_profiles"])
    c["written_to_graph"], c["graph_case_id"] = res["ok"], res["graph_case_id"] if res["ok"] else ""
    if ledger is not None:
        ledger.append(row["case_id"], "graph.write_back", {"ok": res["ok"], "graph_case_id": res["graph_case_id"],
                                                          "edges": res["edges"], "read_back_verified": res["ok"],
                                                          "error": res["error"]})
    if not res["ok"]:
        ans["stop_reason"] += f" Case NOT written to the graph: {res['error']}."
        print(f"  write-back FAILED for {row['case_id']}: {res['error']}", file=sys.stderr)
    AnswerFile.model_validate(ans)


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--case")
    ap.add_argument("--source", choices=("graph", "lab"), default="graph",
                    help="graph = TigerGraph GRAPH_GOA, the source of truth; lab = the local DuckDB mirror (backtests)")
    ap.add_argument("--lab", default=str(ROOT / "data" / "lab.duckdb"))
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--no-write-back", action="store_true",
                    help="do not write the FraudCase into the graph (always off with --source lab)")
    ap.add_argument("--actor", default="sentinel-agent", help="recorded as FraudCase.created_by")
    ap.add_argument("--audit", help="hash-chained audit ledger (default: <out>/../audit/ledger.jsonl)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.source == "graph":
        from sentinel.typologies.tg import GraphUnavailable, TigerGraphTxnSource
        try:
            src = TigerGraphTxnSource()
        except GraphUnavailable as exc:  # fail closed: no silent fallback to the mirror
            print(f"FAILED: {exc}. Nothing written. (--source lab runs on the local mirror, labelled as such.)",
                  file=sys.stderr)
            return 2
    else:
        src = DuckTxnSource(a.lab)
    failed = 0
    log: list[dict] = []
    from sentinel.typologies.audit import Ledger

    ledger = Ledger(a.audit or out.parent / "audit" / "ledger.jsonl")
    for row in csv.DictReader(open(a.pack, encoding="utf8")):
        if a.case and row["case_id"] != a.case:
            continue
        try:
            ans = run_case(row, src, use_model=not a.no_model, log=log, ledger=ledger)
            containment = ans.pop("_containment", None)  # not an answer-format field; written beside it
            if a.source == "graph" and not a.no_write_back:
                write_back(ans, row, src, actor=a.actor, ledger=ledger)
        except Exception as exc:  # fail closed: report, do not write a guess
            failed += 1
            print(f"FAILED {row['case_id']}: {type(exc).__name__}: {exc}", file=sys.stderr)
            ledger.append(row["case_id"], "case.failed", {"error": f"{type(exc).__name__}: {str(exc)[:300]}",
                                                          "written": False, "note": "failed closed; no answer written"})
            continue
        if containment:
            cdir = out.parent / "containment"
            cdir.mkdir(exist_ok=True)
            (cdir / f"{row['case_id']}.json").write_text(json.dumps(containment, indent=1, default=str), encoding="utf8")
            print(f"  containment: block {containment['n_blocked']}/{len(containment['cards'])} cards, cut "
                  f"${containment['exposure_cut']:,.2f} ({containment['coverage']:.0%}), disrupting "
                  f"{len(containment['customers_disrupted'])} customer(s)")
        (out / f"{row['case_id']}.json").write_text(json.dumps(ans, indent=1, ensure_ascii=False), encoding="utf8")
        c = ans["case"]
        ledger.append(row["case_id"], "status.changed", {"from": "new", "to": c["status"], "verdict": c["verdict"],
                                                         "written_to_graph": c["written_to_graph"],
                                                         "answer_file": f"{row['case_id']}.json"})
        print(f"{row['case_id']} {c['verdict']:<10} p={c['fraud_probability']:<6} {c['pattern']:<28} "
              f"exp=${c['exposure_usd']:<9} final={[r['action'] for r in ans['next_best_actions']['final']]} "
              f"sar={ans['sar']['file']}")
    if log:
        path = out.parent / "run_log.csv"
        if path.exists():
            with open(path, encoding="utf8") as fh:
                header = fh.readline().strip().split(",")
            if header != list(RUN_LOG_FIELDS):  # an older layout: keep it, never mix columns
                path.rename(path.with_name(f"run_log.{datetime.now():%Y%m%d%H%M%S}.old.csv"))
        new_file = not path.exists()
        with open(path, "a", newline="", encoding="utf8") as fh:
            w = csv.DictWriter(fh, fieldnames=RUN_LOG_FIELDS)
            if new_file:
                w.writeheader()
            w.writerows(log)
        lat = sorted(r["latency_s"] for r in log)
        p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
        print(f"{len(log)} case(s): median {lat[len(lat) // 2]:.1f}s, p95 {p95:.1f}s, "
              f"{sum(r['graph_queries'] for r in log)} graph queries, {sum(r['model_tokens'] for r in log)} model + {sum(r['embedding_tokens'] for r in log)} embedding tokens"
              f" -> {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
