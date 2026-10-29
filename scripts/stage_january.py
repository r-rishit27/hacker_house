"""Round 2: stage January 2017 alongside the July-December spine.

The final round adds one month of activity in the same 397-column schema and
replaces closed_cases_history.csv. This script applies the *same* projection as
prepare_data.py to the January files, so a January row is indistinguishable
from a December one downstream, and builds a local DuckDB lab over the full
July 2016 - January 2017 picture:

    data/lab.duckdb
      txn           every transaction, Jul-Jan, staged columns
      closed_cases  the replacement history (5,585 cases, incl. HHG-001..020)
      cc_txn        case -> transaction edges
      cc_conn       case -> connected card edges

Card identity must not drift. card_id is card6 ranked within the customer, so a
customer who gains a new card6 value in January would re-rank and silently
rename their December cards. Existing (customer, card6) pairs therefore keep the
id they were staged with; only genuinely new pairs get a fresh -Kn suffix, after
the customer's existing ones. The count of such pairs is printed, because it is
exactly the kind of schema drift that breaks things quietly.

Run:  python scripts/stage_january.py [--dataset-dir data/round2]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from etl import config as cfg  # noqa: E402
from scripts.prepare_data import clean, num, packed  # noqa: E402

LAB = cfg.ROOT / "data" / "lab.duckdb"


def find(d: Path, stem: str) -> Path:
    """Accept the Drive download names ('closed_cases_history (1).csv') and the gzipped copy."""
    hits = sorted(d.glob(f"{stem}*.csv")) or sorted(d.glob(f"{stem}*.csv.gz"))
    if not hits:
        raise SystemExit(f"missing {stem}*.csv in {d}")
    return hits[0]


def write_staging(con: duckdb.DuckDBPyConnection, raw_cc: Path, out: Path) -> None:
    """The loader's thirteen files, recomputed over July - January.

    Same columns and the same aggregate definitions as prepare_data.py, so
    load_to_tigergraph.py loads them unchanged. Baselines (cards, devices,
    regions) are recomputed over the whole span; the alerts are Round 1's,
    because the Round 2 pack is hidden until it is run.
    """
    out.mkdir(parents=True, exist_ok=True)

    def copy(sql: str, name: str) -> None:
        con.execute(f"COPY ({sql}) TO '{(out / name).as_posix()}' (HEADER, DELIMITER ',', QUOTE '\"')")
        n = con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]
        print(f"  staging_r2/{name:<24} {n:>9,}")

    con.execute("CREATE TEMP TABLE t AS SELECT * FROM txn_old UNION ALL BY NAME SELECT * FROM txn_jan")
    con.execute("CREATE TEMP VIEW tt AS SELECT *, TRY_CAST(amt AS DOUBLE) AS a, CAST(ts AS TIMESTAMP) AS tsx FROM t")
    con.execute(
        f"CREATE TEMP TABLE cards_attr AS SELECT * FROM read_csv('{(cfg.STAGING / 'cards.csv').as_posix()}', "
        "all_varchar=true, sample_size=-1)"
    )
    con.execute(
        f"CREATE TEMP TABLE dev_old AS SELECT device_key, label, device_info, device_type, os, browser, screen "
        f"FROM read_csv('{(cfg.STAGING / 'devices.csv').as_posix()}', all_varchar=true, sample_size=-1)"
    )
    copy("SELECT customer_id, count(DISTINCT card_id) AS n_cards, count(*) AS n_txns, min(tsx) AS first_seen, "
         "max(tsx) AS last_seen FROM tt GROUP BY 1 ORDER BY 1", "customers.csv")
    copy(
        """
        SELECT k.card_id, k.customer_id, k.card1, k.card2, k.card3, k.network, k.card5, k.card_type,
               COALESCE(mode(t.addr1) FILTER (WHERE t.addr1 <> ''), '') AS home_region,
               COALESCE(mode(t.addr2) FILTER (WHERE t.addr2 <> ''), '') AS home_country,
               count(*) AS n_txns,
               count(*) FILTER (WHERE t.channel = 'online') AS n_online,
               count(*) FILTER (WHERE t.channel = 'in_person') AS n_in_person,
               round(median(t.a), 2) AS median_amt, round(quantile_cont(t.a, 0.95), 2) AS p95_amt,
               round(max(t.a), 2) AS max_amt, count(DISTINCT t.addr1) AS n_regions,
               count(DISTINCT t.product) AS n_products, string_agg(DISTINCT t.product, '|') AS top_products,
               min(t.tsx) AS first_seen, max(t.tsx) AS last_seen
        FROM tt t JOIN cards_attr k ON k.card_id = t.card_id
        GROUP BY ALL ORDER BY 1
        """, "cards.csv")
    copy(
        """
        WITH lab AS (SELECT device_key, any_value(label) AS label, any_value(device_info) AS device_info,
                            '' AS device_type, any_value(os) AS os, any_value(browser) AS browser,
                            any_value(screen) AS screen FROM ident2 WHERE device_key <> '' GROUP BY 1),
             d AS (SELECT * FROM dev_old UNION ALL SELECT * FROM lab WHERE device_key NOT IN (SELECT device_key FROM dev_old))
        SELECT d.device_key, d.label, d.device_info, d.device_type, d.os, d.browser, d.screen,
               count(*) AS n_txns, count(DISTINCT t.card_id) AS n_cards, count(DISTINCT t.customer_id) AS n_customers
        FROM d JOIN tt t ON t.device_key = d.device_key
        GROUP BY ALL ORDER BY n_cards DESC
        """, "devices.csv")
    copy("SELECT addr1 AS region, mode(addr2) AS country, count(*) AS n_txns, count(DISTINCT card_id) AS n_cards "
         "FROM tt WHERE addr1 <> '' GROUP BY 1 ORDER BY 1", "regions.csv")
    copy("SELECT domain, sum(n) AS n_txns FROM (SELECT p_email AS domain, count(*) n FROM tt WHERE p_email <> '' "
         "GROUP BY 1 UNION ALL SELECT r_email, count(*) FROM tt WHERE r_email <> '' GROUP BY 1) GROUP BY 1 ORDER BY 1",
         "emails.csv")
    copy("SELECT product, CASE WHEN product = 'W' THEN 'in_person' ELSE 'online' END AS channel_class, "
         "count(*) AS n_txns FROM tt WHERE product <> '' GROUP BY 1 ORDER BY 1", "products.csv")
    copy("SELECT txn_id, card_id, customer_id, ts, txn_dt, amt, product, channel, risk_score, addr1, addr2, dist1, "
         "dist2, p_email, r_email, device_key, device_new, proxy, match_status, c_feats, d_feats, m_feats, v_feats "
         "FROM tt ORDER BY tsx", "transactions.csv")
    copy(
        """
        SELECT prev_id AS from_txn, txn_id AS to_txn, gap_seconds FROM (
          SELECT txn_id, lag(txn_id) OVER w AS prev_id,
                 CAST(date_diff('second', lag(tsx) OVER w, tsx) AS BIGINT) AS gap_seconds
          FROM tt WINDOW w AS (PARTITION BY card_id ORDER BY tsx, TRY_CAST(txn_id AS BIGINT))
        ) WHERE prev_id IS NOT NULL
        """, "next_edges.csv")
    cc = f"read_csv('{raw_cc.as_posix()}', all_varchar=true, sample_size=-1)"
    copy(
        f"""
        SELECT case_id, customer_id, card_id, opened_at, closed_at, outcome, pattern,
               COALESCE(first_fraud_txn_id,'') AS first_fraud_txn_id,
               COALESCE(TRY_CAST(n_txns AS BIGINT), 0) AS n_txns,
               COALESCE(TRY_CAST(exposure_usd AS DOUBLE), 0) AS exposure_usd,
               COALESCE(actions_taken,'') AS actions_taken, COALESCE(report_filed,'') AS report_filed,
               {clean('analyst_notes')} AS analyst_notes
        FROM {cc} ORDER BY case_id
        """, "closed_cases.csv")
    copy(f"SELECT case_id, trim(x) AS txn_id FROM {cc}, UNNEST(string_split(COALESCE(txn_ids,''), '|')) AS u(x) "
         "WHERE trim(x) <> ''", "cc_txn_edges.csv")
    copy(f"SELECT case_id, trim(x) AS card_id FROM {cc}, UNNEST(string_split(COALESCE(connected_card_ids,''), '|')) "
         "AS u(x) WHERE trim(x) <> ''", "cc_connected_edges.csv")
    copy(f"SELECT * FROM read_csv('{(cfg.STAGING / 'alerts.csv').as_posix()}', all_varchar=true)", "alerts.csv")
    n = con.execute("SELECT count(*) FROM tt").fetchone()[0]
    copy(f"SELECT meta_id, CASE WHEN meta_id = 'loaded_rows_transactions' THEN '{n}' ELSE value END AS value "
         f"FROM read_csv('{(cfg.STAGING / 'meta.csv').as_posix()}', all_varchar=true) "
         "UNION ALL SELECT 'data_span', '2016-07-02 .. 2017-01-31 (Round 2: January added)' ORDER BY 1", "meta.csv")
    bad = con.execute(
        f"SELECT count(*) FROM {cc} c LEFT JOIN cards_attr k ON k.card_id = c.card_id WHERE k.card_id IS NULL"
    ).fetchone()[0]
    print(f"  [{'ok ' if bad == 0 else 'FAIL'}] closed-case cards not in card set: {bad}")
    if bad:
        raise SystemExit("staging_r2 integrity check failed")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset-dir", default=str(cfg.ROOT / "data" / "round2"))
    ap.add_argument("--skip-drift-approval", action="store_true",
                    help="stage without a schema-drift approval (local experiments only; the run says so)")
    ap.add_argument("--staging-out", default=str(cfg.ROOT / "data" / "staging_r2"),
                    help="where to write the Jul-Jan loader files (point STAGING_DIR here to load them)")
    args = ap.parse_args()
    d = Path(args.dataset_dir)
    # Challenge 12: nothing is staged from changed data until a human with the
    # right role has approved the exact drift report (scripts/schema_drift.py).
    from scripts.schema_drift import approved

    ok, why = approved()
    print(f"schema drift: {why}")
    if not ok and not args.skip_drift_approval:
        raise SystemExit("refusing to stage unapproved data (use --skip-drift-approval for a local experiment)")
    raw_txn, raw_id = find(d, "transactions_2017q1"), find(d, "identity_2017q1")
    raw_cc = find(d, "closed_cases_history")

    LAB.unlink(missing_ok=True)
    con = duckdb.connect(str(LAB))
    con.execute("PRAGMA enable_progress_bar=false")

    print("loading staged Jul-Dec spine")
    con.execute(
        f"CREATE TABLE txn_old AS SELECT * FROM read_csv('{(cfg.STAGING / 'transactions.csv.gz').as_posix()}',"
        " all_varchar=true, sample_size=-1)"
    )
    con.execute(
        f"CREATE TABLE cards_old AS SELECT card_id, customer_id, card_type FROM "
        f"read_csv('{(cfg.STAGING / 'cards.csv').as_posix()}', all_varchar=true, sample_size=-1)"
    )

    print("reading January raw files")
    con.execute(f"CREATE VIEW raw_txn AS SELECT * FROM read_csv('{raw_txn.as_posix()}', all_varchar=true, sample_size=-1)")
    con.execute(f"CREATE VIEW raw_id AS SELECT * FROM read_csv('{raw_id.as_posix()}', all_varchar=true, sample_size=-1)")

    # ---- card identity, stable across the month boundary --------------------
    con.execute(
        """
        CREATE TABLE card_map AS
        WITH jan AS (SELECT DISTINCT customer_id, COALESCE(card6, '') AS ck FROM raw_txn),
        known AS (SELECT customer_id, COALESCE(card_type, '') AS ck, card_id FROM cards_old),
        base AS (SELECT customer_id, max(CAST(split_part(card_id, '-K', 2) AS INT)) AS kmax
                 FROM cards_old GROUP BY 1),
        fresh AS (
          SELECT j.customer_id, j.ck,
                 j.customer_id || '-K' || (COALESCE(b.kmax, 0)
                   + row_number() OVER (PARTITION BY j.customer_id ORDER BY j.ck)) AS card_id
          FROM jan j
          LEFT JOIN known k ON k.customer_id = j.customer_id AND k.ck = j.ck
          LEFT JOIN base b ON b.customer_id = j.customer_id
          WHERE k.card_id IS NULL)
        SELECT customer_id, ck, card_id, false AS is_new FROM known
        UNION ALL SELECT customer_id, ck, card_id, true FROM fresh
        """
    )
    n_new = con.execute("SELECT count(*) FROM card_map WHERE is_new").fetchone()[0]
    print(f"  new (customer, card6) pairs in January: {n_new}")

    # ---- identity / device profiles, same rule as prepare_data.py -----------
    con.execute(
        f"""
        CREATE TABLE ident AS
        SELECT "TransactionID" AS txn_id,
               {clean('DeviceInfo')} AS device_info,
               COALESCE("id_30", '') AS os, COALESCE("id_31", '') AS browser,
               COALESCE("id_33", '') AS screen, COALESCE("id_15", '') AS device_new,
               COALESCE("id_23", '') AS proxy, COALESCE("id_34", '') AS match_status
        FROM raw_id
        """
    )
    con.execute(
        """
        CREATE TABLE ident2 AS
        SELECT *, CASE WHEN label IS NULL OR label = '' THEN '' ELSE 'D' || substr(md5(label), 1, 12) END AS device_key
        FROM (SELECT *, concat_ws(' | ', NULLIF(device_info,''), NULLIF(os,''), NULLIF(browser,''), NULLIF(screen,'')) AS label
              FROM ident)
        """
    )

    con.execute(
        f"""
        CREATE TABLE txn_jan AS
        SELECT t."TransactionID" AS txn_id, m.card_id, t.customer_id, t.ts,
               CAST(COALESCE(TRY_CAST(t."TransactionDT" AS BIGINT), -1) AS VARCHAR) AS txn_dt,
               CAST({num('TransactionAmt')} AS VARCHAR) AS amt,
               COALESCE(t."ProductCD", '') AS product, COALESCE(t.channel, '') AS channel,
               CAST({num('risk_score')} AS VARCHAR) AS risk_score,
               COALESCE(t."addr1", '') AS addr1, COALESCE(t."addr2", '') AS addr2,
               CAST({num('dist1')} AS VARCHAR) AS dist1, CAST({num('dist2')} AS VARCHAR) AS dist2,
               COALESCE(t."P_emaildomain", '') AS p_email, COALESCE(t."R_emaildomain", '') AS r_email,
               COALESCE(i.device_key, '') AS device_key, COALESCE(i.device_new, '') AS device_new,
               COALESCE(i.proxy, '') AS proxy, COALESCE(i.match_status, '') AS match_status,
               {packed(cfg.C_COLS)} AS c_feats, {packed(cfg.D_COLS)} AS d_feats,
               {packed(cfg.M_COLS)} AS m_feats, {packed(cfg.V_COLS)} AS v_feats
        FROM raw_txn t
        JOIN card_map m ON m.customer_id = t.customer_id AND m.ck = COALESCE(t.card6, '')
        LEFT JOIN ident2 i ON i.txn_id = t."TransactionID"
        """
    )
    con.execute(
        "CREATE TABLE device_labels AS SELECT DISTINCT device_key, label FROM ident2 WHERE device_key <> ''"
    )
    n_raw = con.execute("SELECT count(*) FROM raw_txn").fetchone()[0]
    n_jan = con.execute("SELECT count(*) FROM txn_jan").fetchone()[0]
    if n_raw != n_jan:
        raise SystemExit(f"January join dropped rows: {n_raw} raw vs {n_jan} staged")

    # ---- the unified spine, typed -------------------------------------------
    con.execute(
        """
        CREATE TABLE txn AS
        SELECT CAST(txn_id AS BIGINT) AS txn_id, card_id, customer_id, CAST(ts AS TIMESTAMP) AS ts,
               CAST(amt AS DOUBLE) AS amt, product, channel, CAST(risk_score AS DOUBLE) AS risk_score,
               NULLIF(NULLIF(addr1, ''), '-1.0') AS addr1, NULLIF(addr2, '') AS addr2,
               CAST(dist1 AS DOUBLE) AS dist1, NULLIF(p_email, '') AS p_email, NULLIF(r_email, '') AS r_email,
               NULLIF(device_key, '') AS device_key, NULLIF(device_new, '') AS device_new,
               NULLIF(proxy, '') AS proxy, NULLIF(match_status, '') AS match_status,
               c_feats, d_feats, m_feats, v_feats, src
        FROM (SELECT *, 'jul_dec' AS src FROM txn_old UNION ALL BY NAME SELECT *, 'jan' AS src FROM txn_jan)
        """
    )
    write_staging(con, raw_cc, Path(args.staging_out))
    con.execute("DROP TABLE txn_old")
    con.execute("DROP TABLE txn_jan")

    # ---- closed cases --------------------------------------------------------
    con.execute(f"CREATE TABLE closed_cases AS SELECT * FROM read_csv('{raw_cc.as_posix()}', all_varchar=true, sample_size=-1)")
    con.execute(
        """
        CREATE TABLE cc_txn AS
        SELECT case_id, CAST(unnest(string_split(txn_ids, '|')) AS BIGINT) AS txn_id
        FROM closed_cases WHERE txn_ids IS NOT NULL AND txn_ids <> ''
        """
    )
    con.execute(
        """
        CREATE TABLE cc_conn AS
        SELECT case_id, unnest(string_split(connected_card_ids, '|')) AS card_id
        FROM closed_cases WHERE connected_card_ids IS NOT NULL AND connected_card_ids <> ''
        """
    )

    for t in ("txn", "closed_cases", "cc_txn", "cc_conn", "card_map", "device_labels"):
        print(f"  {t:<14} {con.execute(f'SELECT count(*) FROM {t}').fetchone()[0]:>9,}")
    print(con.execute("SELECT src, min(ts), max(ts), count(*) FROM txn GROUP BY 1 ORDER BY 2").fetchall())
    con.close()
    print("wrote", LAB)


if __name__ == "__main__":
    main()
