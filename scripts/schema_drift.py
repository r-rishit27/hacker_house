"""Schema drift survival (challenge 12): detect, explain, and wait for a human.

January arrives as new files and a *replaced* closed-case history. Before the
pipeline acts on them:

    python scripts/schema_drift.py check     # write data/round2/drift_report.json, print what changed
    python scripts/schema_drift.py approve --token <sv1...>   # a team lead or fraud manager approves it

``scripts/stage_january.py`` refuses to stage unless an approval exists for the
*exact* report (by content hash) — a change in the files after approval
invalidates it. The approval is a signed identity (api/auth.py), not a name typed
into a field, and an analyst's token is refused.

What is compared:
  - the transaction and identity columns the pipeline reads (the contract in
    etl/config.py and scripts/prepare_data.py): present, parseable, null rate
  - categorical domains the rules depend on (ProductCD, channel, card6, id_15, id_23)
  - id continuity and ordering against the July - December spine
  - card identity: (customer, card6) pairs January would add or re-rank
  - the closed-case history: rows added, removed, and *changed* against Round 1's copy
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
from etl import config as cfg  # noqa: E402

DATA = ROOT / "data" / "round2"
REPORT = DATA / "drift_report.json"
APPROVAL = DATA / "drift_approval.json"
APPROVER_ROLES = {"team_lead", "fraud_manager"}

TXN_CONTRACT = {
    "TransactionID": "BIGINT", "TransactionDT": "BIGINT", "TransactionAmt": "DOUBLE", "ProductCD": "VARCHAR",
    "card4": "VARCHAR", "card6": "VARCHAR", "addr1": "DOUBLE", "addr2": "DOUBLE", "dist1": "DOUBLE",
    "P_emaildomain": "VARCHAR", "R_emaildomain": "VARCHAR", "customer_id": "VARCHAR", "ts": "TIMESTAMP",
    "channel": "VARCHAR", "risk_score": "DOUBLE",
    **{c: "VARCHAR" for c in cfg.C_COLS + cfg.D_COLS + cfg.M_COLS + cfg.V_COLS},
}
ID_CONTRACT = {"TransactionID": "BIGINT", "DeviceInfo": "VARCHAR", "DeviceType": "VARCHAR", "id_15": "VARCHAR",
               "id_23": "VARCHAR", "id_30": "VARCHAR", "id_31": "VARCHAR", "id_33": "VARCHAR", "id_34": "VARCHAR"}
DOMAINS = {"ProductCD": {"W", "C", "H", "R", "S"}, "channel": {"online", "in_person"},
           "card6": {"credit", "debit", "charge card", "debit or credit"}}
ID_DOMAINS = {"id_15": {"New", "Found", "Unknown"}, "id_23": {"IP_PROXY:TRANSPARENT", "IP_PROXY:ANONYMOUS", "IP_PROXY:HIDDEN"}}


def _file(stem: str) -> Path:
    hits = sorted(DATA.glob(f"{stem}*.csv")) or sorted(DATA.glob(f"{stem}*.csv.gz"))
    if not hits:
        raise SystemExit(f"missing {stem} in {DATA}")
    return hits[0]


def check() -> dict:
    con = duckdb.connect()
    t, i, cc = _file("transactions_2017q1"), _file("identity_2017q1"), _file("closed_cases_history")
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_csv('{t.as_posix()}', all_varchar=true, sample_size=-1)")
    con.execute(f"CREATE VIEW i AS SELECT * FROM read_csv('{i.as_posix()}', all_varchar=true, sample_size=-1)")
    findings: list[dict] = []

    def columns(view, contract, label):
        cols = [r[0] for r in con.execute(f"DESCRIBE {view}").fetchall()]
        n = con.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
        for c, typ in contract.items():
            if c not in cols:
                findings.append({"severity": "breaking", "where": label, "column": c, "change": "missing column"})
                continue
            bad = con.execute(f'SELECT count(*) FROM {view} WHERE "{c}" IS NOT NULL AND TRY_CAST("{c}" AS {typ}) IS NULL').fetchone()[0]
            nul = con.execute(f'SELECT count(*) FILTER (WHERE "{c}" IS NULL) FROM {view}').fetchone()[0]
            if bad:
                findings.append({"severity": "breaking", "where": label, "column": c,
                                 "change": f"{bad} values do not parse as {typ}"})
            if c in ("TransactionID", "customer_id", "ts", "TransactionAmt") and nul:
                findings.append({"severity": "breaking", "where": label, "column": c, "change": f"{nul} nulls in a key"})
        return cols, n

    tcols, tn = columns("t", TXN_CONTRACT, "transactions")
    icols, inn = columns("i", ID_CONTRACT, "identity")
    for view, domains, label in (("t", DOMAINS, "transactions"), ("i", ID_DOMAINS, "identity")):
        for c, allowed in domains.items():
            vals = {r[0] for r in con.execute(f'SELECT DISTINCT "{c}" FROM {view} WHERE "{c}" IS NOT NULL').fetchall()}
            if vals - allowed:
                findings.append({"severity": "review", "where": label, "column": c,
                                 "change": f"new values {sorted(vals - allowed)[:8]}"})
    old_max = 3590742
    lo, hi = con.execute("SELECT min(TRY_CAST(TransactionID AS BIGINT)), max(TRY_CAST(TransactionID AS BIGINT)) FROM t").fetchone()
    if lo <= old_max:
        findings.append({"severity": "breaking", "where": "transactions", "column": "TransactionID",
                         "change": f"ids overlap the July-December spine (min {lo})"})
    dec = con.execute("""WITH s AS (SELECT TRY_CAST(TransactionID AS BIGINT) id, CAST(ts AS TIMESTAMP) ts FROM t),
                              w AS (SELECT (ts < lag(ts) OVER (ORDER BY id))::INT back FROM s)
                         SELECT avg(back) FROM w""").fetchone()[0]
    if dec and dec > 0.01:
        findings.append({"severity": "review", "where": "transactions", "column": "TransactionID",
                         "change": f"ids no longer follow time: {dec:.0%} of consecutive ids go back in time "
                                   "(July-December ids were time-ordered). Nothing may assume id order = time order."})
    cards = con.execute(f"""WITH j AS (SELECT DISTINCT customer_id, COALESCE(card6,'') ck FROM t),
        k AS (SELECT customer_id, COALESCE(card_type,'') ck FROM read_csv('{(cfg.ROOT / 'data' / 'staging' / 'cards.csv').as_posix()}', all_varchar=true))
        SELECT count(*) FROM j LEFT JOIN k USING (customer_id, ck) WHERE k.customer_id IS NULL""").fetchone()[0]
    if cards:
        findings.append({"severity": "breaking", "where": "card identity", "column": "card6",
                         "change": f"{cards} new (customer, card6) pairs would re-rank existing card ids; "
                                   "stage_january.py appends them after the existing -Kn instead"})
    old_cc = ROOT / "closed_cases_history.csv"
    con.execute(f"CREATE VIEW o AS SELECT * FROM read_csv('{old_cc.as_posix()}', all_varchar=true, sample_size=-1)")
    con.execute(f"CREATE VIEW n AS SELECT * FROM read_csv('{cc.as_posix()}', all_varchar=true, sample_size=-1)")
    added = [r[0] for r in con.execute("SELECT case_id FROM n EXCEPT SELECT case_id FROM o ORDER BY 1").fetchall()]
    removed = [r[0] for r in con.execute("SELECT case_id FROM o EXCEPT SELECT case_id FROM n ORDER BY 1").fetchall()]
    changed = [r[0] for r in con.execute("SELECT case_id FROM (SELECT * FROM n EXCEPT SELECT * FROM o) "
                                         "WHERE case_id IN (SELECT case_id FROM o) ORDER BY 1").fetchall()]
    findings.append({"severity": "review" if not (removed or changed) else "breaking", "where": "closed cases",
                     "column": "*", "change": f"{len(added)} added ({added[:3]}..{added[-1:]}) , {len(removed)} removed, "
                                              f"{len(changed)} changed", "added": added, "removed": removed, "changed": changed})
    body = {"checked_at": datetime.now().isoformat(timespec="seconds"),
            "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (t, i, cc)},
            "transactions": {"rows": tn, "columns": len(tcols), "id_range": [lo, hi]},
            "identity": {"rows": inn, "columns": len(icols)},
            "findings": findings,
            "remapping": {"card_id": "existing (customer, card6) pairs keep their -Kn; new pairs are appended",
                          "closed_cases": "replace the Round 1 copy; HHG-001..020 become CC-5566..5585",
                          "transactions": "append January to the July-December spine; never infer time from id"}}
    body["report_hash"] = hashlib.sha256(json.dumps({k: v for k, v in body.items() if k != "checked_at"},
                                                    sort_keys=True, default=str).encode()).hexdigest()
    REPORT.write_text(json.dumps(body, indent=1, default=str), encoding="utf8")
    return body


def _receipt(report_hash: str, sub: str, role: str, at: str) -> str:
    """Server-side proof of the approval: only the holder of SENTINEL_AUTH_SECRET can
    make or check it. The bearer token itself is never stored — it is a credential."""
    import hmac

    from api import auth

    return hmac.new(auth.secret() or b"", f"drift|{report_hash}|{sub}|{role}|{at}".encode(), hashlib.sha256).hexdigest()


def approve(token: str) -> dict:
    from api import auth

    body = json.loads(REPORT.read_text(encoding="utf8"))
    claims = auth.verify(token)
    if claims.role.value not in APPROVER_ROLES:
        raise SystemExit(f"refused: {claims.sub} is {claims.role.value}; schema changes need {sorted(APPROVER_ROLES)}")
    at = datetime.now().isoformat(timespec="seconds")
    rec = {"report_hash": body["report_hash"], "files": body["files"], "approved_by": claims.sub,
           "role": claims.role.value, "approved_at": at,
           "receipt": _receipt(body["report_hash"], claims.sub, claims.role.value, at)}
    APPROVAL.write_text(json.dumps(rec, indent=1), encoding="utf8")
    return rec


def approved() -> tuple[bool, str]:
    """For stage_january.py: is the current data covered by an approval?"""
    import hmac

    from api import auth

    if not APPROVAL.exists():
        return False, "no approval: run `python scripts/schema_drift.py check`, then `approve --token ...`"
    rec = json.loads(APPROVAL.read_text(encoding="utf8"))
    # Bound to the bytes of the three files the report was computed from: any change
    # to them after approval invalidates it, without re-running the whole check.
    current = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
               for p in (_file("transactions_2017q1"), _file("identity_2017q1"), _file("closed_cases_history"))}
    if rec.get("files") != current:
        return False, "the data changed after it was approved; re-check and re-approve"
    if rec.get("role") not in APPROVER_ROLES:
        return False, f"approval by role {rec.get('role')} is not sufficient"
    if not auth.secret():
        return True, (f"approved by {rec['approved_by']} ({rec['role']}) at {rec['approved_at']} "
                      "[receipt not verified here: SENTINEL_AUTH_SECRET is not set]")
    want = _receipt(rec["report_hash"], rec["approved_by"], rec["role"], rec["approved_at"])
    if not hmac.compare_digest(want, rec.get("receipt", "")):
        return False, "approval receipt does not verify against this server's secret"
    return True, f"approved by {rec['approved_by']} ({rec['role']}) at {rec['approved_at']}, receipt verified"


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    a = sub.add_parser("approve")
    a.add_argument("--token", required=True)
    args = ap.parse_args()
    if args.cmd == "check":
        body = check()
        for f in body["findings"]:
            print(f"  [{f['severity']:<8}] {f['where']:<14} {f['column']:<14} {f['change'][:110]}")
        print(f"report {REPORT.relative_to(ROOT)}  hash {body['report_hash'][:16]}  -> needs approval by {sorted(APPROVER_ROLES)}")
    else:
        rec = approve(args.token)
        print(f"approved by {rec['approved_by']} ({rec['role']}) for report {rec['report_hash'][:16]}")


if __name__ == "__main__":
    main()
