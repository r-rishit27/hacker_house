"""Replay Round 1's twenty cases through the Round 2 decision layer and score
them against the real outcomes (CC-5566..5585).

Scoring mirrors the Round 2 brief: a block or report on a legitimate customer
is a zero and is counted separately; ``uncertain`` is shown as its own column.

Run:  python -m eval.replay_round1 [--no-model]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

from sentinel.typologies.decide import decide  # noqa: E402
from sentinel.typologies.duck import DuckTxnSource  # noqa: E402

load_dotenv(ROOT / ".env")


def prior_cases(src, card: str, before: datetime) -> list[dict]:
    return src.prior_cases(card, before)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--pack", default=str(ROOT / "case_pack.csv"))
    args = ap.parse_args()
    src = DuckTxnSource(ROOT / "data" / "lab.duckdb")
    truth = {r[0].split()[1]: r[1] for r in src.con.execute(
        "SELECT analyst_notes, outcome FROM closed_cases WHERE analyst_notes LIKE 'Case HHG-%'").fetchall()}
    rows, score = [], {"correct": 0, "uncertain": 0, "wrong": 0, "harmed_legit": 0}
    for row in csv.DictReader(open(args.pack, encoding="utf8")):
        as_of = datetime.fromisoformat(row["opened_at"])
        d = decide(case_id=row["case_id"], trigger_type=row["trigger_type"], trigger_text=row["trigger_text"],
                   card_id=row["card_id"], flagged_txn_id=int(row["flagged_txn_id"]), as_of=as_of, src=src,
                   prior_cases=prior_cases(src, row["card_id"], as_of), use_model=not args.no_model)
        t = {"confirmed_fraud": "fraud", "cleared": "legitimate"}.get(truth.get(row["case_id"], ""), "?")
        if d.verdict == "uncertain":
            score["uncertain"] += 1
        elif d.verdict == t:
            score["correct"] += 1
        else:
            score["wrong"] += 1
            score["harmed_legit"] += t == "legitimate"
        a = d.adjudication
        rows.append({"case": row["case_id"], "truth": t, "verdict": d.verdict, "p": d.fraud_probability,
                     "pattern": d.pattern, "by": d.decided_by, "unexplained": d.unexplained,
                     "model": a and {"verdict": a.verdict, "p": a.fraud_probability, "rejected": a.rejected,
                                     "tokens": a.tokens, "latency_s": round(a.latency_s, 1), "why": a.rationale[:300]}})
        mark = "OK " if d.verdict == t else ("?  " if d.verdict == "uncertain" else "XX ")
        print(f"{mark}{row['case_id']} truth={t:<10} got={d.verdict:<10} p={d.fraud_probability:<6} by={d.decided_by:<20} "
              f"{d.pattern:<28} {('model:' + a.verdict + ' ' + (a.rejected[:60] or a.rationale[:90])) if a else ''}")
    print(score)
    (ROOT / "eval" / "replay_round1.json").write_text(json.dumps({"score": score, "cases": rows}, indent=1, default=str))


if __name__ == "__main__":
    main()
