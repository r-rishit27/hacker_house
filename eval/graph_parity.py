"""Does the decision layer reach the same answer from TigerGraph as from the lab mirror?

Runs every Round 1 case through ``decide`` twice — once on GRAPH_GOA, once on
data/lab.duckdb — without the model, and compares verdict, probability, pattern,
affected transactions, connected cards, detector hits and explanations.

Run:  python -m eval.graph_parity
"""

from __future__ import annotations

import csv
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

from sentinel.typologies.decide import decide  # noqa: E402
from sentinel.typologies.duck import DuckTxnSource  # noqa: E402
from sentinel.typologies.tg import TigerGraphTxnSource  # noqa: E402

load_dotenv(ROOT / ".env")


def key(d):
    return (d.verdict, d.fraud_probability, d.pattern, tuple(d.affected_txn_ids), tuple(d.connected_card_ids),
            tuple(sorted(h.key for h in d.hits)), tuple(d.explanations), tuple(d.unexplained), d.decided_by)


def main() -> None:
    lab, tg = DuckTxnSource(ROOT / "data" / "lab.duckdb"), TigerGraphTxnSource()
    same = 0
    for row in csv.DictReader(open(ROOT / "case_pack.csv", encoding="utf8")):
        out = []
        for src in (lab, tg):
            t0, c0 = time.perf_counter(), src.calls
            as_of = datetime.fromisoformat(row["opened_at"])
            d = decide(case_id=row["case_id"], trigger_type=row["trigger_type"], trigger_text=row["trigger_text"],
                       card_id=row["card_id"], flagged_txn_id=int(row["flagged_txn_id"]), as_of=as_of, src=src,
                       prior_cases=src.prior_cases(row["card_id"], as_of), use_model=False)
            out.append((key(d), time.perf_counter() - t0, src.calls - c0))
        ok = out[0][0] == out[1][0]
        same += ok
        print(f"{row['case_id']} {'SAME' if ok else 'DIFF'}  {out[1][0][0]:<10} graph {out[1][1]:.1f}s/{out[1][2]} queries"
              + ("" if ok else f"\n   lab:   {out[0][0]}\n   graph: {out[1][0]}"))
    print(f"{same}/20 identical")


if __name__ == "__main__":
    main()
