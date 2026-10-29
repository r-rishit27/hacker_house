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
                  "model", "decided_by", "verdict", "fraud_probability")


def run_case(row: dict, src: DuckTxnSource, *, use_model: bool, log: list | None = None) -> dict:
    t0, calls0 = time.perf_counter(), src.calls
    started = datetime.now().isoformat(timespec="seconds")
    as_of = datetime.fromisoformat(row["opened_at"])
    priors = prior_cases(src, row["card_id"], as_of)
    d = decide(case_id=row["case_id"], trigger_type=row["trigger_type"], trigger_text=row.get("trigger_text", ""),
               card_id=row["card_id"], flagged_txn_id=int(row["flagged_txn_id"]), as_of=as_of, src=src,
               prior_cases=priors, use_model=use_model)
    ans = build_answer(d, case_id=row["case_id"], trigger=row["trigger_type"], card_id=row["card_id"],
                       customer_id=row["customer_id"], flagged_txn_id=int(row["flagged_txn_id"]), as_of=as_of,
                       src=src, prior_cases=priors, tool_calls=src.calls - calls0,
                       latency_s=time.perf_counter() - t0)
    if log is not None:
        a = d.adjudication
        # tokens = the decision model's tokens; it is the only model this pipeline calls.
        # tool_calls = graph (or mirror) queries. latency = wall time, graph + model + policy.
        log.append({"case_id": row["case_id"], "started_at": started, "source": getattr(src, "label", "?"),
                    "latency_s": ans["latency_s"], "graph_queries": ans["tool_calls"],
                    "model_calls": int(a is not None), "model_tokens": a.tokens if a else 0,
                    "model": a.model if a else "", "decided_by": d.decided_by, "verdict": d.verdict,
                    "fraud_probability": d.fraud_probability})
    return ans


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
    for row in csv.DictReader(open(a.pack, encoding="utf8")):
        if a.case and row["case_id"] != a.case:
            continue
        try:
            ans = run_case(row, src, use_model=not a.no_model, log=log)
        except Exception as exc:  # fail closed: report, do not write a guess
            failed += 1
            print(f"FAILED {row['case_id']}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        (out / f"{row['case_id']}.json").write_text(json.dumps(ans, indent=1, ensure_ascii=False), encoding="utf8")
        c = ans["case"]
        print(f"{row['case_id']} {c['verdict']:<10} p={c['fraud_probability']:<6} {c['pattern']:<28} "
              f"exp=${c['exposure_usd']:<9} final={[r['action'] for r in ans['next_best_actions']['final']]} "
              f"sar={ans['sar']['file']}")
    if log:
        path = out.parent / "run_log.csv"
        new_file = not path.exists()
        with open(path, "a", newline="", encoding="utf8") as fh:
            w = csv.DictWriter(fh, fieldnames=RUN_LOG_FIELDS)
            if new_file:
                w.writeheader()
            w.writerows(log)
        lat = sorted(r["latency_s"] for r in log)
        p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
        print(f"{len(log)} case(s): median {lat[len(lat) // 2]:.1f}s, p95 {p95:.1f}s, "
              f"{sum(r['graph_queries'] for r in log)} graph queries, {sum(r['model_tokens'] for r in log)} model tokens"
              f" -> {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
