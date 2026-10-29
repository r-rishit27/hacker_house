"""Is the decision layer overfitted to Round 1's twenty cases?

The Round 2 rules were written while looking at HHG-001..020, so a good score
there proves little. This script measures the same layer on data none of the
rules were tuned on, and takes each rule away in turn.

  replay     HHG-001..020 against their real outcomes (in-sample, for reference)
  cleared    the bank's own cleared alerts, run as score alerts: the innocent
             customers we would wrongly call fraud (the Round 2 penalty)
  frauds     historical confirmed frauds, run as customer reports: frauds lost
  january    the January scenario groups (fraud groups and look-alike groups)
  ablation   replay with each Round 2 rule switched off

Run:  python -m eval.overfit_check [--model] [--n 300]
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from eval.fit_lookalike import build_cleared_alerts  # noqa: E402
from eval.replay_round1 import prior_cases  # noqa: E402
from sentinel.typologies import decide as D  # noqa: E402
from sentinel.typologies.duck import DuckTxnSource  # noqa: E402

load_dotenv(ROOT / ".env")

JANUARY = [  # (name, first id, last id, expected) — see docs/ROUND2_FINDINGS.md
    ("card-present look-alikes", 3660000, 3660095, "legitimate"),
    ("new device + anonymous proxy", 3660096, 3660130, "fraud"),
    ("escalation / dormant", 3660131, 3660173, "fraud"),
    ("micro charges", 3660174, 3660260, "fraud"),
    ("one recipient", 3660261, 3660289, "fraud"),
    ("weekly subscriptions", 3660299, 3660310, "legitimate"),
    ("usual-amount look-alikes", 3660321, 3660345, "legitimate"),
    ("card testing", 3660346, 3660350, "fraud"),
]


def run(src, cid, trig, card, tid, as_of, model, priors=True):
    return D.decide(case_id=cid, trigger_type=trig, trigger_text="", card_id=card, flagged_txn_id=tid,
                    as_of=as_of, src=src, prior_cases=prior_cases(src, card, as_of) if priors else [],
                    use_model=model)


def replay(src, model) -> dict:
    truth = {r[0].split()[1]: {"confirmed_fraud": "fraud", "cleared": "legitimate"}[r[1]] for r in src.con.execute(
        "SELECT analyst_notes, outcome FROM closed_cases WHERE analyst_notes LIKE 'Case HHG-%'").fetchall()}
    s = Counter()
    for row in csv.DictReader(open(ROOT / "case_pack.csv", encoding="utf8")):
        d = run(src, row["case_id"], row["trigger_type"], row["card_id"], int(row["flagged_txn_id"]),
                datetime.fromisoformat(row["opened_at"]), model)
        t = truth[row["case_id"]]
        s["correct" if d.verdict == t else "uncertain" if d.verdict == "uncertain" else "wrong"] += 1
        s["harmed"] += t == "legitimate" and d.verdict == "fraud"
    return dict(s)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="store_true", help="include the decisions model (costs API calls)")
    ap.add_argument("--n", type=int, default=300)
    a = ap.parse_args()
    src = DuckTxnSource(ROOT / "data" / "lab.duckdb")
    rng = random.Random(5)
    report: dict = {"model": a.model}

    report["replay"] = replay(src, a.model)
    print("replay (in-sample):", report["replay"])

    build_cleared_alerts(src)
    rows = src.con.execute("SELECT a.case_id, a.card_id, a.txn_id, c.opened_at FROM cleared_alert a "
                           "JOIN closed_cases c USING (case_id) WHERE a.case_id < 'CC-5566'").fetchall()
    rng.shuffle(rows)
    v, by_month = Counter(), Counter()
    for cid, card, tid, op in rows[: a.n]:
        d = run(src, cid, "risk_score", card, tid, datetime.fromisoformat(op), a.model)
        v[d.verdict] += 1
        by_month[(op[:7], d.verdict == "fraud")] += 1
    n = sum(v.values())
    report["cleared"] = {"n": n, **v, "innocent_called_fraud": round(v["fraud"] / n, 4)}
    print(f"cleared alerts (held out): {dict(v)}  innocent called fraud = {v['fraud'] / n:.1%}")

    rows = src.con.execute("SELECT case_id, card_id, opened_at, CAST(first_fraud_txn_id AS BIGINT) FROM closed_cases "
                           "WHERE outcome='confirmed_fraud' AND first_fraud_txn_id <> '' AND case_id < 'CC-5566'").fetchall()
    rng.shuffle(rows)
    v = Counter(run(src, c, "customer_report", card, tid, datetime.fromisoformat(op), a.model).verdict
                for c, card, op, tid in rows[: a.n])
    report["frauds_as_reports"] = dict(v)
    print("historical frauds as customer reports (held out):", dict(v))

    jan = {}
    for name, lo, hi, expect in JANUARY:
        c = Counter()
        for card, last in src.con.execute(
                "SELECT card_id, max(ts) FROM txn WHERE txn_id BETWEEN ? AND ? GROUP BY 1", [lo, hi]).fetchall():
            tid = src.con.execute("SELECT txn_id FROM txn WHERE card_id=? AND txn_id BETWEEN ? AND ? ORDER BY ts DESC LIMIT 1",
                                  [card, lo, hi]).fetchone()[0]
            c[run(src, card, "risk_score", card, tid, last + timedelta(hours=1), a.model).verdict] += 1
        jan[name] = {"expect": expect, **c}
        print(f"  january {name:<30} expect={expect:<10} {dict(c)}")
    report["january"] = jan

    report["ablation"] = {}
    for rule in D.RULES:
        D.RULES[rule] = False
        report["ablation"][rule] = replay(src, a.model)
        D.RULES[rule] = True
        print(f"  replay without {rule:<15}", report["ablation"][rule])

    out = ROOT / "eval" / ("overfit_check_model.json" if a.model else "overfit_check.json")
    out.write_text(json.dumps(report, indent=1))
    print("wrote", out)


if __name__ == "__main__":
    main()
