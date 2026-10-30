"""Fit the fraud-or-look-alike model on the bank's closed cases, and test it on
Round 1's twenty, which are held out of training.

  negatives  833 cleared alerts, matched back to their flagged transaction
  positives  confirmed-fraud cases, at their first fraudulent transaction
  held out   CC-5566..5585 (= HHG-001..020) are excluded from training

Reports 5-fold cross-validated AUC for logistic regression and gradient
boosting, picks thresholds on out-of-fold scores, and writes the logistic
weights to backend/sentinel/typologies/lookalike.json.

Run:  python -m eval.fit_lookalike
"""

from __future__ import annotations

import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sentinel.typologies.duck import DuckTxnSource  # noqa: E402
from sentinel.typologies.lookalike import FEATURES, WEIGHTS, features  # noqa: E402

HELD_OUT = {f"CC-{i:04d}" for i in range(5566, 5586)}


def build_cleared_alerts(src: DuckTxnSource) -> None:
    src.con.execute(r"""
        CREATE OR REPLACE TEMP TABLE cleared_alert AS
        WITH c AS (SELECT case_id, card_id, CAST(opened_at AS TIMESTAMP) op,
                   TRY_CAST(replace(regexp_extract(analyst_notes, 'scored a \$([0-9,.]+)', 1), ',', '') AS DOUBLE) amt,
                   TRY_CAST(regexp_extract(analyst_notes, ' at ([0-9.]+)\.', 1) AS DOUBLE) rs
                   FROM closed_cases WHERE outcome = 'cleared')
        SELECT c.case_id, c.card_id, arg_max(t.txn_id, t.ts) AS txn_id
        FROM c JOIN txn t ON t.card_id = c.card_id AND abs(t.amt - c.amt) < 0.005 AND abs(t.risk_score - c.rs) < 0.005
             AND t.ts <= c.op AND t.ts > c.op - INTERVAL 7 DAY
        GROUP BY ALL""")


def dataset(src: DuckTxnSource, n_pos: int, rng: random.Random):
    build_cleared_alerts(src)
    neg = src.con.execute("SELECT case_id, card_id, txn_id FROM cleared_alert").fetchall()
    pos = src.con.execute(
        "SELECT case_id, card_id, CAST(first_fraud_txn_id AS BIGINT) FROM closed_cases "
        "WHERE outcome = 'confirmed_fraud' AND first_fraud_txn_id <> ''").fetchall()
    neg = [r for r in neg if r[0] not in HELD_OUT]
    pos = rng.sample([r for r in pos if r[0] not in HELD_OUT], n_pos)
    cards = sorted({r[1] for r in neg + pos})
    rows = src.con.execute(
        "SELECT txn_id, card_id, customer_id, ts, amt, product, channel, addr1, p_email, r_email, device_key, "
        "device_new, proxy, risk_score FROM txn WHERE card_id IN (SELECT unnest(?::VARCHAR[])) ORDER BY ts",
        [cards]).fetchall()
    from sentinel.typologies.model import Txn
    by_card = defaultdict(list)
    for r in rows:
        by_card[r[1]].append(Txn(*r))
    X, y, ids = [], [], []
    for label, group in ((0, neg), (1, pos)):
        for cid, card, tid in group:
            txns = by_card[card]
            f = next((t for t in txns if t.txn_id == tid), None)
            if f is None:
                continue
            hist = [t for t in txns if t.ts < f.ts]
            if len(hist) < 3:
                continue
            x = features(f, hist)
            X.append([x[k] for k in FEATURES]); y.append(label); ids.append(cid)
    return np.array(X), np.array(y), ids


def round1(src: DuckTxnSource):
    out = []
    for row in csv.DictReader(open(ROOT / "case_pack.csv", encoding="utf8")):
        txns = src.card_txns(row["card_id"], src.con.execute(
            "SELECT max(ts) + INTERVAL 1 SECOND FROM txn").fetchone()[0])
        f = next(t for t in txns if t.txn_id == int(row["flagged_txn_id"]))
        x = features(f, [t for t in txns if t.ts < f.ts])
        out.append((row["case_id"], row["trigger_type"], [x[k] for k in FEATURES]))
    return out


def main() -> None:
    rng = random.Random(11)
    src = DuckTxnSource(ROOT / "data" / "lab.duckdb")
    X, y, _ = dataset(src, n_pos=2500, rng=rng)
    print(f"training rows: {len(y)}  fraud={y.sum()}  cleared={(1 - y).sum()}")
    cv = StratifiedKFold(5, shuffle=True, random_state=0)
    sc = StandardScaler().fit(X)
    lr = LogisticRegression(max_iter=2000, C=0.5, class_weight="balanced")
    p_lr = cross_val_predict(lr, sc.transform(X), y, cv=cv, method="predict_proba")[:, 1]
    gb = HistGradientBoostingClassifier(max_depth=4, learning_rate=0.08, max_iter=250, class_weight="balanced")
    p_gb = cross_val_predict(gb, X, y, cv=cv, method="predict_proba")[:, 1]
    print(f"5-fold AUC  logistic={roc_auc_score(y, p_lr):.3f}  boosting={roc_auc_score(y, p_gb):.3f}")

    # Thresholds on out-of-fold logistic scores: clear only where <=2% of frauds fall,
    # call fraud only where <=2% of cleared alerts reach.
    lo = float(np.quantile(p_lr[y == 1], 0.02))
    hi = float(np.quantile(p_lr[y == 0], 0.98))
    for name, p in (("logistic", p_lr), ("boosting", p_gb)):
        l2, h2 = float(np.quantile(p[y == 1], 0.02)), float(np.quantile(p[y == 0], 0.98))
        print(f"  {name}: clear<= {l2:.3f} clears {np.mean(p[y == 0] <= l2):.1%} of cleared; "
              f"fraud>= {h2:.3f} catches {np.mean(p[y == 1] >= h2):.1%} of fraud")

    lr.fit(sc.transform(X), y)
    gb.fit(X, y)
    coef = dict(zip(FEATURES, lr.coef_[0].round(4).tolist()))
    print("logistic weights (standardised):", sorted(coef.items(), key=lambda kv: -abs(kv[1]))[:10])
    WEIGHTS.write_text(json.dumps({
        "mean": dict(zip(FEATURES, sc.mean_.tolist())), "scale": dict(zip(FEATURES, sc.scale_.tolist())),
        "coef": coef, "intercept": float(lr.intercept_[0]), "thresholds": {"legit_at": lo, "fraud_at": hi},
        "meta": {"n_train": int(len(y)), "n_fraud": int(y.sum()), "n_cleared": int((1 - y).sum()),
                 "cv_auc_logistic": float(roc_auc_score(y, p_lr)), "cv_auc_boosting": float(roc_auc_score(y, p_gb)),
                 "held_out": "CC-5566..5585 (HHG-001..020)"},
    }, indent=1))
    print("wrote", WEIGHTS)

    truth = {r[0].split()[1]: r[1] for r in src.con.execute(
        "SELECT analyst_notes, outcome FROM closed_cases WHERE analyst_notes LIKE 'Case HHG-%'").fetchall()}
    print("\nheld-out Round 1 cases (never trained on):")
    for cid, trig, x in round1(src):
        pl = lr.predict_proba(sc.transform([x]))[0, 1]
        pg = gb.predict_proba([x])[0, 1]
        call = "fraud" if pl >= hi else "legit" if pl <= lo else "uncertain"
        print(f"  {cid} {truth[cid]:<15} {trig:<15} logistic={pl:.3f} boosting={pg:.3f} -> {call}")


if __name__ == "__main__":
    main()
