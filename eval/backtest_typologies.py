"""Backtest the typology detectors and the look-alike explainers.

Four measurements, each printed with the ids behind it:

  1. Round 1 replay   HHG-001..020 at their opened_at, against the real outcomes
                      now in closed_cases_history (CC-5566..5585).
  2. Recall           per documented/undocumented pattern, over confirmed-fraud
                      closed cases, detectors run at the case's opened_at.
  3. False positives  cards never in any case, at a random January time: how often
                      does any detector fire on a card nobody reported?
  4. January block    every card in the scenario block 3660000-3660355.

Run:  python -m eval.backtest_typologies [--controls 400] [--recall 60]
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sentinel.typologies import explain, run_detectors  # noqa: E402
from sentinel.typologies.duck import DuckTxnSource  # noqa: E402

LAB = ROOT / "data" / "lab.duckdb"
OUT = ROOT / "eval" / "backtest_typologies.json"


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def investigate(src: DuckTxnSource, card: str, flagged: int | None, as_of: datetime) -> dict:
    hits = run_detectors(card, src, as_of)
    exps = []
    if flagged is not None and (f := src.txn(flagged)):
        hist = [t for t in src.card_txns(card, f.ts)]
        after = [t for t in src.card_txns(card, as_of) if t.ts > f.ts]
        exps = explain(f, hist, after)
    return {
        "hits": [{"key": h.key, "n_txns": len(h.txn_ids), "n_connected": len(h.connected_card_ids),
                  "strength": h.strength, "desc": h.description} for h in hits],
        "explanations": [e.key for e in exps],
    }


def round1(src: DuckTxnSource) -> list[dict]:
    truth = {}
    for r in src.con.execute("SELECT analyst_notes, outcome, pattern FROM closed_cases WHERE analyst_notes LIKE 'Case HHG-%'").fetchall():
        truth[r[0].split()[1]] = (r[1], r[2])
    out = []
    with open(ROOT / "case_pack.csv", encoding="utf8") as fh:
        for row in csv.DictReader(fh):
            res = investigate(src, row["card_id"], int(row["flagged_txn_id"]), _ts(row["opened_at"]))
            out.append({"case": row["case_id"], "truth": truth[row["case_id"]], **res})
    return out


def recall(src: DuckTxnSource, per_pattern: int, rng: random.Random) -> dict:
    rows = src.con.execute(
        "SELECT case_id, card_id, opened_at, pattern, first_fraud_txn_id FROM closed_cases WHERE outcome='confirmed_fraud'"
    ).fetchall()
    by = defaultdict(list)
    for r in rows:
        by[r[3]].append(r)
    res = {}
    for pat, rs in sorted(by.items()):
        sample = rs if len(rs) <= per_pattern else rng.sample(rs, per_pattern)
        fired = Counter()
        anyhit = 0
        for cid, card, opened, _, _ in sample:
            hits = run_detectors(card, src, _ts(opened))
            anyhit += bool(hits)
            fired.update(h.key for h in hits)
        res[pat] = {"n": len(sample), "any": round(anyhit / len(sample), 3), "by_detector": dict(fired)}
    return res


def controls(src: DuckTxnSource, n: int, rng: random.Random) -> dict:
    cards = [r[0] for r in src.con.execute(
        "SELECT DISTINCT card_id FROM txn WHERE src='jan' AND txn_id < 3660000 AND card_id NOT IN "
        "(SELECT card_id FROM closed_cases UNION SELECT card_id FROM cc_conn) "
        "AND card_id NOT IN (SELECT card_id FROM txn WHERE txn_id >= 3660000)"
    ).fetchall()]
    sample = rng.sample(sorted(cards), min(n, len(cards)))
    fired = Counter()
    examples = defaultdict(list)
    for card in sample:
        as_of = datetime(2017, 1, 1) + timedelta(seconds=rng.randint(10, 30) * 86400)
        for h in run_detectors(card, src, as_of):
            fired[h.key] += 1
            if len(examples[h.key]) < 3:
                examples[h.key].append({"card": card, "as_of": as_of.isoformat(), "desc": h.description})
    return {"n": len(sample), "rate_by_detector": {k: round(v / len(sample), 4) for k, v in fired.items()},
            "examples": examples}


def january_block(src: DuckTxnSource) -> list[dict]:
    rows = src.con.execute(
        "SELECT card_id, min(txn_id), max(ts) FROM txn WHERE txn_id >= 3660000 GROUP BY 1 ORDER BY 2"
    ).fetchall()
    out = []
    for card, id0, last in rows:
        res = investigate(src, card, id0, last + timedelta(seconds=1))
        out.append({"card": card, "first_block_txn": id0, **res})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--controls", type=int, default=400)
    ap.add_argument("--recall", type=int, default=60)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    src = DuckTxnSource(LAB)

    report = {"round1": round1(src)}
    print("== Round 1 replay")
    for r in report["round1"]:
        print(f"  {r['case']} {r['truth'][0]:<15} {r['truth'][1]:<28} hits={[h['key'] for h in r['hits']]} expl={r['explanations']}")

    report["january_block"] = january_block(src)
    print("== January scenario block")
    for r in report["january_block"]:
        print(f"  {r['card']:<10} {r['first_block_txn']} hits={[(h['key'], h['n_connected']) for h in r['hits']]} expl={r['explanations']}")

    report["controls"] = controls(src, args.controls, rng)
    print("== Controls (cards never in a case, January):", report["controls"]["n"], report["controls"]["rate_by_detector"])

    report["recall"] = recall(src, args.recall, rng)
    print("== Recall on confirmed-fraud closed cases")
    for p, v in report["recall"].items():
        print(f"  {p:<28} n={v['n']:<4} any={v['any']:<6} {v['by_detector']}")

    report["queries"] = src.calls
    OUT.write_text(json.dumps(report, indent=1, default=str))
    print("wrote", OUT, "queries:", src.calls)


if __name__ == "__main__":
    main()
