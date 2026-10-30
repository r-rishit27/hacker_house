"""Stage 1 of the load: turn the four raw CSVs into narrow, TigerGraph-shaped files.

Why this exists: transactions.csv is 708 MB across 393 columns. Uploading it as
delivered is hours of pain for no analytic gain, and Savanna caps a single
upload well below that size. This script does the projection described in
PRD 6.3 -- every one of the 590,742 rows, a curated column set -- and derives
the things the raw files only imply:

  * card_id          the card6 value (credit / debit / charge card) ranked
                     lexicographically within the customer -> C08623-K1, -K2.
                     This rule was not guessed: it was recovered by testing
                     candidate rules against the 14,975 txn -> card_id pairs
                     implied by closed_cases_history.csv and case_pack.csv, and
                     it reproduces all 14,975 exactly. A tuple-based rule looks
                     plausible and gets 10 of the 20 exam cards wrong.
  * device_key       stable hash of "DeviceInfo | OS | browser | screen", the
                     exact label answer files must quote.
  * card baselines   median/p95 amount, home region, product mix -- what the
                     agent compares a flagged transaction against.
  * NEXT edges       transaction ordering within a card, computed with a window
                     function here rather than in GSQL.

Everything is written to data/staging/. Run:  python scripts/prepare_data.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from etl import config as cfg  # noqa: E402

OUT = cfg.STAGING


def q(name: str) -> str:
    return f'"{name}"'


def num(col: str) -> str:
    """Cast a varchar column to DOUBLE, mapping missing to the -1 sentinel."""
    return f"COALESCE(TRY_CAST({q(col)} AS DOUBLE), {cfg.MISSING_NUM})"


def packed(cols: list[str]) -> str:
    """Pipe-join a family of columns, preserving position for missing values."""
    parts = ", ".join(f"COALESCE({q(c)}, '')" for c in cols)
    return f"concat_ws('|', {parts})"


def clean(col: str) -> str:
    """Strip characters that would break a CSV round-trip through the loader."""
    return f"regexp_replace(COALESCE({q(col)}, ''), '[\\r\\n\"]', ' ', 'g')"


def copy_out(con: duckdb.DuckDBPyConnection, sql: str, filename: str) -> int:
    path = OUT / filename
    t0 = time.time()
    con.execute(f"COPY ({sql}) TO '{path.as_posix()}' (HEADER, DELIMITER ',', QUOTE '\"')")
    rows = con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]
    size = path.stat().st_size / 1e6
    print(f"  {filename:<26} {rows:>9,} rows  {size:>8.1f} MB  ({time.time() - t0:.1f}s)")
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for f in (cfg.RAW_TRANSACTIONS, cfg.RAW_IDENTITY, cfg.RAW_CLOSED_CASES, cfg.RAW_CASE_PACK):
        if not f.exists():
            raise SystemExit(f"missing raw file: {f}")

    con = duckdb.connect()
    con.execute("PRAGMA enable_progress_bar=false")
    print("reading raw CSVs (all columns as text, so nothing is silently coerced)")
    con.execute(
        f"CREATE VIEW raw_txn AS SELECT * FROM read_csv('{cfg.RAW_TRANSACTIONS.as_posix()}',"
        " all_varchar=true, sample_size=-1)"
    )
    con.execute(
        f"CREATE VIEW raw_id AS SELECT * FROM read_csv('{cfg.RAW_IDENTITY.as_posix()}',"
        " all_varchar=true, sample_size=-1)"
    )
    con.execute(
        f"CREATE VIEW raw_cc AS SELECT * FROM read_csv('{cfg.RAW_CLOSED_CASES.as_posix()}',"
        " all_varchar=true, sample_size=-1)"
    )
    con.execute(
        f"CREATE VIEW raw_pack AS SELECT * FROM read_csv('{cfg.RAW_CASE_PACK.as_posix()}',"
        " all_varchar=true, sample_size=-1)"
    )

    # ---- card identity -------------------------------------------------------
    print("deriving card identities")
    con.execute(
        """
        CREATE TABLE card_key AS
        SELECT customer_id, COALESCE(card6, '') AS ck, count(*) AS n
        FROM raw_txn GROUP BY 1, 2
        """
    )
    con.execute(
        """
        CREATE TABLE card_map AS
        SELECT customer_id, ck,
               customer_id || '-K' || row_number() OVER (PARTITION BY customer_id ORDER BY ck ASC) AS card_id
        FROM card_key
        """
    )
    # Per-card issuer attributes, taken as the modal value across the card's rows.
    con.execute(
        """
        CREATE TABLE card_attrs AS
        SELECT m.card_id, m.customer_id,
               COALESCE(mode(t.card1), '') AS card1,
               COALESCE(mode(t.card2), '') AS card2,
               COALESCE(mode(t.card3), '') AS card3,
               COALESCE(mode(t.card4), '') AS network,
               COALESCE(mode(t.card5), '') AS card5,
               m.ck                        AS card_type
        FROM raw_txn t
        JOIN card_map m ON m.customer_id = t.customer_id AND m.ck = COALESCE(t.card6, '')
        GROUP BY m.card_id, m.customer_id, m.ck
        """
    )

    # ---- identity records ----------------------------------------------------
    print("building device profiles")
    con.execute(
        f"""
        CREATE TABLE ident AS
        SELECT "TransactionID" AS txn_id,
               {clean('DeviceInfo')} AS device_info,
               COALESCE("DeviceType", '')  AS device_type,
               COALESCE("id_30", '')       AS os,
               COALESCE("id_31", '')       AS browser,
               COALESCE("id_33", '')       AS screen,
               COALESCE("id_15", '')       AS device_new,
               COALESCE("id_23", '')       AS proxy,
               COALESCE("id_34", '')       AS match_status
        FROM raw_id
        """
    )
    # The label is the exact string answer files must quote in
    # connected_device_profiles, so it is built once and reused everywhere.
    con.execute(
        """
        CREATE TABLE ident2 AS
        SELECT *,
               concat_ws(' | ', NULLIF(device_info,''), NULLIF(os,''),
                                NULLIF(browser,''), NULLIF(screen,'')) AS label
        FROM ident
        """
    )
    con.execute(
        """
        CREATE TABLE ident3 AS
        SELECT *, CASE WHEN label IS NULL OR label = '' THEN ''
                       ELSE 'D' || substr(md5(label), 1, 12) END AS device_key
        FROM ident2
        """
    )

    # ---- the transaction spine ----------------------------------------------
    print("joining transactions to cards and identity")
    con.execute(
        f"""
        CREATE TABLE txn AS
        SELECT t."TransactionID"                       AS txn_id,
               m.card_id                               AS card_id,
               t.customer_id                           AS customer_id,
               t.ts                                    AS ts,
               COALESCE(TRY_CAST(t."TransactionDT" AS BIGINT), -1) AS txn_dt,
               {num('TransactionAmt')}                 AS amt,
               COALESCE(t."ProductCD", '')             AS product,
               COALESCE(t.channel, '')                 AS channel,
               {num('risk_score')}                     AS risk_score,
               COALESCE(t."addr1", '')                 AS addr1,
               COALESCE(t."addr2", '')                 AS addr2,
               {num('dist1')}                          AS dist1,
               {num('dist2')}                          AS dist2,
               COALESCE(t."P_emaildomain", '')         AS p_email,
               COALESCE(t."R_emaildomain", '')         AS r_email,
               COALESCE(i.device_key, '')              AS device_key,
               COALESCE(i.device_new, '')              AS device_new,
               COALESCE(i.proxy, '')                   AS proxy,
               COALESCE(i.match_status, '')            AS match_status,
               {packed(cfg.C_COLS)}                    AS c_feats,
               {packed(cfg.D_COLS)}                    AS d_feats,
               {packed(cfg.M_COLS)}                    AS m_feats,
               {packed(cfg.V_COLS)}                    AS v_feats
        FROM raw_txn t
        JOIN card_map m
          ON m.customer_id = t.customer_id AND m.ck = COALESCE(t.card6, '')
        LEFT JOIN ident3 i ON i.txn_id = t."TransactionID"
        """
    )

    # ---- write staging files -------------------------------------------------
    print("writing staging files to", OUT)

    copy_out(
        con,
        """
        SELECT customer_id,
               count(DISTINCT card_id) AS n_cards,
               count(*)                AS n_txns,
               min(ts)                 AS first_seen,
               max(ts)                 AS last_seen
        FROM txn GROUP BY 1 ORDER BY 1
        """,
        "customers.csv",
    )

    # Card baselines: what "normal" looks like for this cardholder. The agent
    # compares flagged activity against these, never against global thresholds.
    con.execute(
        """
        CREATE TABLE cards AS
        SELECT t.card_id, t.customer_id,
               any_value(k.card1) AS card1, any_value(k.card2) AS card2, any_value(k.card3) AS card3,
               any_value(k.network) AS network, any_value(k.card5) AS card5, any_value(k.card_type) AS card_type,
               mode(t.addr1) FILTER (WHERE t.addr1 <> '') AS home_region,
               mode(t.addr2) FILTER (WHERE t.addr2 <> '') AS home_country,
               count(*) AS n_txns,
               count(*) FILTER (WHERE t.channel = 'online')    AS n_online,
               count(*) FILTER (WHERE t.channel = 'in_person') AS n_in_person,
               median(t.amt) AS median_amt,
               quantile_cont(t.amt, 0.95) AS p95_amt,
               max(t.amt) AS max_amt,
               count(DISTINCT t.addr1) AS n_regions,
               count(DISTINCT t.product) AS n_products,
               string_agg(DISTINCT t.product, '|') AS top_products,
               min(t.ts) AS first_seen, max(t.ts) AS last_seen
        FROM txn t
        JOIN card_attrs k ON k.card_id = t.card_id
        GROUP BY 1, 2
        """
    )
    copy_out(
        con,
        """
        SELECT card_id, customer_id, card1, card2, card3, network, card5, card_type,
               COALESCE(home_region,'') AS home_region, COALESCE(home_country,'') AS home_country,
               n_txns, n_online, n_in_person,
               round(median_amt,2) AS median_amt, round(p95_amt,2) AS p95_amt, round(max_amt,2) AS max_amt,
               n_regions, n_products, top_products, first_seen, last_seen
        FROM cards ORDER BY card_id
        """,
        "cards.csv",
    )

    copy_out(
        con,
        """
        SELECT d.device_key, any_value(d.label) AS label, any_value(d.device_info) AS device_info,
               any_value(d.device_type) AS device_type, any_value(d.os) AS os,
               any_value(d.browser) AS browser, any_value(d.screen) AS screen,
               count(*) AS n_txns,
               count(DISTINCT t.card_id) AS n_cards,
               count(DISTINCT t.customer_id) AS n_customers
        FROM ident3 d JOIN txn t ON t.txn_id = d.txn_id
        WHERE d.device_key <> ''
        GROUP BY d.device_key ORDER BY n_cards DESC
        """,
        "devices.csv",
    )

    copy_out(
        con,
        """
        SELECT addr1 AS region, mode(addr2) AS country, count(*) AS n_txns,
               count(DISTINCT card_id) AS n_cards
        FROM txn WHERE addr1 <> '' GROUP BY 1 ORDER BY 1
        """,
        "regions.csv",
    )

    copy_out(
        con,
        """
        SELECT domain, sum(n) AS n_txns FROM (
          SELECT p_email AS domain, count(*) n FROM txn WHERE p_email <> '' GROUP BY 1
          UNION ALL
          SELECT r_email AS domain, count(*) n FROM txn WHERE r_email <> '' GROUP BY 1
        ) GROUP BY 1 ORDER BY 1
        """,
        "emails.csv",
    )

    copy_out(
        con,
        """
        SELECT product,
               CASE WHEN product = 'W' THEN 'in_person' ELSE 'online' END AS channel_class,
               count(*) AS n_txns
        FROM txn WHERE product <> '' GROUP BY 1 ORDER BY 1
        """,
        "products.csv",
    )

    n_txn = copy_out(
        con,
        """
        SELECT txn_id, card_id, customer_id, ts, txn_dt, amt, product, channel, risk_score,
               addr1, addr2, dist1, dist2, p_email, r_email, device_key, device_new, proxy,
               match_status, c_feats, d_feats, m_feats, v_feats
        FROM txn ORDER BY ts
        """,
        "transactions.csv",
    )

    copy_out(
        con,
        """
        SELECT prev_id AS from_txn, txn_id AS to_txn, gap_seconds FROM (
          SELECT txn_id,
                 lag(txn_id) OVER (PARTITION BY card_id ORDER BY ts, txn_id) AS prev_id,
                 CAST(date_diff('second',
                      lag(CAST(ts AS TIMESTAMP)) OVER (PARTITION BY card_id ORDER BY ts, txn_id),
                      CAST(ts AS TIMESTAMP)) AS BIGINT) AS gap_seconds
          FROM txn
        ) WHERE prev_id IS NOT NULL
        """,
        "next_edges.csv",
    )

    # ---- closed cases: the labelled history and the agent's seed memory ------
    copy_out(
        con,
        f"""
        SELECT case_id, customer_id, card_id, opened_at, closed_at, outcome, pattern,
               COALESCE(first_fraud_txn_id,'') AS first_fraud_txn_id,
               COALESCE(TRY_CAST(n_txns AS BIGINT), 0) AS n_txns,
               COALESCE(TRY_CAST(exposure_usd AS DOUBLE), 0) AS exposure_usd,
               COALESCE(actions_taken,'') AS actions_taken,
               COALESCE(report_filed,'') AS report_filed,
               {clean('analyst_notes')} AS analyst_notes
        FROM raw_cc ORDER BY case_id
        """,
        "closed_cases.csv",
    )

    copy_out(
        con,
        """
        SELECT case_id, trim(txn_id) AS txn_id
        FROM raw_cc, UNNEST(string_split(COALESCE(txn_ids,''), '|')) AS u(txn_id)
        WHERE trim(txn_id) <> ''
        """,
        "cc_txn_edges.csv",
    )

    copy_out(
        con,
        """
        SELECT case_id, trim(u.conn_card) AS card_id
        FROM raw_cc, UNNEST(string_split(COALESCE(connected_card_ids,''), '|')) AS u(conn_card)
        WHERE trim(u.conn_card) <> ''
        """,
        "cc_connected_edges.csv",
    )

    # ---- the 20 exam alerts, so the work queue lives in the graph ------------
    copy_out(
        con,
        f"""
        SELECT case_id AS alert_id, opened_at, trigger_type, {clean('trigger_text')} AS trigger_text,
               flagged_txn_id, card_id, customer_id,
               COALESCE(TRY_CAST(risk_score AS DOUBLE), -1) AS risk_score,
               'new' AS status
        FROM raw_pack ORDER BY case_id
        """,
        "alerts.csv",
    )

    # ---- loader contracts ----------------------------------------------------
    meta = [
        ("packed_order.c_feats", "|".join(cfg.C_COLS)),
        ("packed_order.d_feats", "|".join(cfg.D_COLS)),
        ("packed_order.m_feats", "|".join(cfg.M_COLS)),
        ("packed_order.v_feats", "|".join(cfg.V_COLS)),
        ("missing_numeric_sentinel", str(cfg.MISSING_NUM)),
        ("card_id_rule", "card6 value ranked lexicographically within customer; verified against 14,975 ground-truth pairs"),
        ("device_key_rule", "D + md5('DeviceInfo | OS | browser | screen')[:12]"),
        ("source", "IEEE-CIS Fraud Detection (Vesta) via TigerGraph HHGOA 2026"),
        ("loaded_rows_transactions", str(n_txn)),
    ]
    con.execute("CREATE TABLE meta(meta_id VARCHAR, value VARCHAR)")
    con.executemany("INSERT INTO meta VALUES (?, ?)", meta)
    copy_out(con, "SELECT meta_id, value FROM meta ORDER BY 1", "meta.csv")

    # ---- integrity checks before anything touches TigerGraph -----------------
    print("\nintegrity checks")
    checks = {
        "transactions with no card_id": "SELECT count(*) FROM txn WHERE card_id IS NULL",
        "closed-case cards not in card set":
            "SELECT count(*) FROM raw_cc c LEFT JOIN cards k ON k.card_id=c.card_id WHERE k.card_id IS NULL",
        "case-pack cards not in card set":
            "SELECT count(*) FROM raw_pack p LEFT JOIN cards k ON k.card_id=p.card_id WHERE k.card_id IS NULL",
        "case-pack txns not in txn set":
            "SELECT count(*) FROM raw_pack p LEFT JOIN txn t ON t.txn_id=p.flagged_txn_id WHERE t.txn_id IS NULL",
        "case-pack txn on a different card than the alert claims":
            "SELECT count(*) FROM raw_pack p JOIN txn t ON t.txn_id=p.flagged_txn_id WHERE t.card_id <> p.card_id",
    }
    failed = False
    for label, sql in checks.items():
        n = con.execute(sql).fetchone()[0]
        flag = "ok " if n == 0 else "FAIL"
        if n:
            failed = True
        print(f"  [{flag}] {label}: {n}")

    print("\nstaging complete ->", OUT)
    if failed:
        print("  one or more checks failed; fix before loading")
        sys.exit(1)


if __name__ == "__main__":
    main()
