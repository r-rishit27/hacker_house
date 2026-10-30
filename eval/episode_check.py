"""Episode scoping against the bank's own labelled episodes.

For historical confirmed-fraud cases: start from the first fraudulent
transaction, scope with ``sentinel.typologies.episode.scope`` using only data up
to the case's opened_at, and compare with the case's ``txn_ids``.

Run:  python -m eval.episode_check [--n 300]
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sentinel.typologies.duck import DuckTxnSource  # noqa: E402
from sentinel.typologies.episode import scope  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    a = ap.parse_args()
    src = DuckTxnSource(ROOT / "data" / "lab.duckdb")
    rows = src.con.execute(
        "SELECT case_id, card_id, opened_at, CAST(first_fraud_txn_id AS BIGINT), txn_ids, "
        "CAST(exposure_usd AS DOUBLE) FROM closed_cases WHERE outcome='confirmed_fraud' AND first_fraud_txn_id <> ''"
    ).fetchall()
    random.Random(9).shuffle(rows)
    stats = Counter()
    jac, exp_err = [], []
    for cid, card, op, first, ids, exposure in rows[: a.n]:
        as_of = datetime.fromisoformat(op)
        txns = src.card_txns(card, as_of)
        f = next((t for t in txns if t.txn_id == first), None)
        if f is None:
            stats["flagged_not_visible"] += 1
            continue
        truth = {int(x) for x in ids.split("|")}
        for label, got in (("flagged_only", {first}), ("scoped", set(scope(f, txns)))):
            j = len(got & truth) / len(got | truth)
            stats[f"{label}_exact"] += got == truth
            if label == "scoped":
                jac.append(j)
                amt = sum(t.amt for t in txns if t.txn_id in got)
                exp_err.append(abs(amt - exposure))
                stats["sar_trigger_agrees"] += (amt > 1000) == (exposure > 1000)
                stats["over_scoped"] += bool(got - truth)
                stats["under_scoped"] += bool(truth - got)
        stats["n"] += 1
    n = stats["n"]
    print(f"n={n}  exact: flagged-only {stats['flagged_only_exact'] / n:.1%}  scoped {stats['scoped_exact'] / n:.1%}")
    print(f"scoped mean Jaccard {sum(jac) / n:.3f}  median |exposure error| ${sorted(exp_err)[n // 2]:,.2f}  "
          f"§3a $1,000 trigger agrees {stats['sar_trigger_agrees'] / n:.1%}")
    print(f"over-scoped {stats['over_scoped'] / n:.1%}  under-scoped {stats['under_scoped'] / n:.1%}  "
          f"(flagged not visible: {stats['flagged_not_visible']})")


if __name__ == "__main__":
    main()
