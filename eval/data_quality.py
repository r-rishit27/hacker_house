"""Data quality detective (challenge 11): the gaps that matter, and the cases they taint.

Bad data looks like fraud, or hides it. For the whole July 2016 – January 2017
dataset this finds, with affected record ids:

  - transactions with no identity record (device / proxy / match signals unobservable)
  - online transactions with no identity record (a new-device fraud cannot be seen)
  - empty billing region (region novelty and out-of-region use cannot be tested)
  - placeholder values (dist1 = -1 sentinel, risk_score missing)
  - device profiles with no usable label (a ring cannot be named)
  - cards whose history is too thin to judge (< 10 earlier transactions)
  - January cards with no history before January (baselines undefined)

Then, for every investigation in a case pack, which of these gaps touch the
flagged transaction or its card — and so which conclusions cannot be trusted at
face value, and why.

Run:  python -m eval.data_quality [--pack case_pack.csv] [--answers eval/out/round1/cases]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import duckdb  # noqa: E402

THIN = 10


def problems(con) -> dict:
    q = lambda sql: con.execute(sql).fetchall()  # noqa: E731
    out = {}

    def add(key, what, where, why):
        n, total = q(f"SELECT count(*) FILTER (WHERE {where}), count(*) FROM txn")[0]
        sample = [r[0] for r in q(f"SELECT txn_id FROM txn WHERE {where} ORDER BY txn_id LIMIT 10")]
        by_month = dict(q(f"SELECT strftime(ts, '%Y-%m'), count(*) FILTER (WHERE {where}) FROM txn GROUP BY 1 ORDER BY 1"))
        out[key] = {"what": what, "why_it_matters": why, "n": n, "share": round(n / total, 4),
                    "sample_txn_ids": sample, "by_month": by_month}

    add("no_identity_record", "transactions with no identity record", "device_key IS NULL",
        "device, proxy and identity-match signals are unobservable, not absent; a rule reading NULL as 'no new "
        "device' would clear new-device fraud")
    add("online_no_identity", "online transactions with no identity record", "channel = 'online' AND device_key IS NULL",
        "an online purchase with no device record cannot be tested for card-not-present new-device fraud at all")
    add("no_billing_region", "transactions with an empty billing region", "addr1 IS NULL",
        "out-of-region use and travel cannot be tested; a region rule must skip, not fire")
    add("dist_placeholder", "dist1 holds the -1 'missing' sentinel", "dist1 = -1",
        "distance features are placeholders, not zero distance")
    add("risk_missing", "risk_score missing (-1 sentinel)", "risk_score < 0",
        "a missing score is not a low score")
    n_dev = q("SELECT count(DISTINCT device_key) FROM txn WHERE device_key IS NOT NULL")[0][0]
    unlabelled = q("SELECT count(*) FROM device_labels WHERE label IS NULL OR label = ''")[0][0]
    out["device_label_missing"] = {"what": "January device profiles with no usable label", "n": unlabelled,
                                   "of": n_dev, "why_it_matters": "connected_device_profiles must quote a label"}
    thin = q(f"""WITH c AS (SELECT card_id, count(*) n FROM txn GROUP BY 1) SELECT count(*), list(card_id ORDER BY card_id)[1:10]
                 FROM c WHERE n < {THIN}""")[0]
    out["thin_cards"] = {"what": f"cards with fewer than {THIN} transactions in the whole dataset", "n": thin[0],
                         "sample_card_ids": thin[1], "why_it_matters": "no stable baseline: amount, region and "
                         "product comparisons are not meaningful"}
    new_jan = q("""SELECT count(*), list(card_id ORDER BY card_id)[1:10] FROM (SELECT card_id, min(ts) t0 FROM txn GROUP BY 1)
                   WHERE t0 >= TIMESTAMP '2017-01-01'""")[0]
    out["new_in_january"] = {"what": "cards first seen in January 2017", "n": new_jan[0],
                             "sample_card_ids": new_jan[1], "why_it_matters": "the bank's model was not retrained "
                             "and these cards have no pre-January baseline"}
    return out


def taint(con, pack: Path, answers: Path | None) -> list[dict]:
    rows = []
    for r in csv.DictReader(open(pack, encoding="utf8")):
        f = con.execute("SELECT txn_id, channel, device_key, addr1, risk_score, dist1, ts FROM txn WHERE txn_id = ?",
                        [int(r["flagged_txn_id"])]).fetchone()
        prior = con.execute("SELECT count(*) FROM txn WHERE card_id = ? AND ts < ?", [r["card_id"], f[6]]).fetchone()[0]
        issues = []
        if f[2] is None:
            issues.append("flagged transaction has no identity record: device and proxy signals unobservable"
                          + (" on an ONLINE purchase" if f[1] == "online" else ""))
        if f[3] is None:
            issues.append("flagged transaction has no billing region: region novelty untestable")
        if prior < THIN:
            issues.append(f"card has only {prior} earlier transactions: baseline too thin")
        if f[4] is not None and f[4] < 0:
            issues.append("risk score missing")
        verdict = ""
        if answers and (answers / f"{r['case_id']}.json").exists():
            verdict = json.loads((answers / f"{r['case_id']}.json").read_text(encoding="utf8"))["case"]["verdict"]
        # Tainted = a gap that removes evidence the verdict would have needed.
        tainted = bool(issues) and (verdict in ("legitimate", "uncertain") or prior < THIN)
        rows.append({"case_id": r["case_id"], "flagged_txn_id": r["flagged_txn_id"], "card_id": r["card_id"],
                     "verdict": verdict, "prior_txns": prior, "issues": issues, "tainted": tainted,
                     "note": ("the conclusion rests on evidence the data cannot show; treat as lower confidence"
                              if tainted else "")})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default=str(ROOT / "case_pack.csv"))
    ap.add_argument("--answers", default=str(ROOT / "eval" / "out" / "round1" / "cases"))
    a = ap.parse_args()
    con = duckdb.connect(str(ROOT / "data" / "lab.duckdb"), read_only=True)
    report = {"problems": problems(con), "investigations": taint(con, Path(a.pack), Path(a.answers))}
    for k, v in report["problems"].items():
        print(f"  {k:<22} {v['n']:>8,}" + (f"  ({v['share']:.1%})" if "share" in v else "") + f"  {v['what']}")
    t = [r for r in report["investigations"] if r["tainted"]]
    print(f"investigations with a data gap: {sum(bool(r['issues']) for r in report['investigations'])}; "
          f"tainted: {len(t)} -> {[r['case_id'] for r in t]}")
    for r in report["investigations"]:
        if r["issues"]:
            print(f"    {r['case_id']} ({r['verdict'] or '-'}): " + "; ".join(r["issues"]))
    (ROOT / "eval" / "data_quality.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf8")
    print("wrote eval/data_quality.json")


if __name__ == "__main__":
    main()
