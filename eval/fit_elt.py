"""Fit the Evidence Likelihood Table from the bank's closed cases.

This is what makes `fraud_probability` calibrated rather than guessed. For each
binary piece of evidence e we compute a likelihood ratio

    LR(e) = P(e | fraud) / P(e | not fraud)

and the agent accumulates log LR over the evidence it actually found. An LLM
asked "how likely is this fraud?" returns 0.85 for anything that reads as scary;
this returns a number fitted on 14,055 confirmed-fraud transactions.

## The trap this file is built to avoid

The obvious design -- fraud cases as positives, the 900 cleared cases as
negatives -- is invalid on this dataset, and invalid in a way that looks fine
until you check how each class was triggered:

    outcome           trigger           n        mean risk   % risk >= 0.7
    cleared           model score         900      0.881         100%
    confirmed_fraud   customer report   4,656      0.475          26%

Every cleared case was raised by the model. Every confirmed case was raised by a
customer. There is not one cleared customer report and not one confirmed
model-score alert in the whole history. The classes are perfectly separated by
*trigger pathway*, so a likelihood ratio fitted across them measures how the
alert arrived, not whether it was fraud. Fitted that way, `risk_85_100` came out
at LR 0.12 and `device_new` at 0.19 -- "a high model score and a brand new
device are evidence AGAINST fraud" -- which is an artefact of the cleared
population being made of model-scored new-device alerts that cardholders then
confirmed ("confirmed travel" 716, "confirmed new phone" 158).

So this file separates two questions the naive fit conflates:

1. **How suspicious is this transaction, for this card?** Fitted here by
   case-control matching: the negatives are other transactions **on the same
   cards**, outside any case, in the same period. Matching on card removes
   card-level confounding (a card whose median is $400 should not make $300 look
   anomalous) and is immune to the trigger artefact, because both classes are
   drawn from the same cards.

2. **How suspicious is the alert, given how it arrived?** That is the prior, and
   it is reported separately rather than smuggled into the LRs.

Cleared-case rates are still computed and stored per feature as
`p_given_cleared_alert`, for transparency, but the ratios are not built from
them.

Features are computed as of each transaction, using only what was knowable
before it. A "region is novel" feature that peeks at the whole file would clear
real fraud and flag real customers; see docs/HAND_INVESTIGATION.md.

    python -m eval.fit_elt            # writes sentinel/elt.json
    python -m eval.fit_elt --report   # also print the full table
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from etl import config as cfg  # noqa: E402

OUT_PATH = cfg.ROOT / "sentinel" / "elt.json"
NL = "\n"

# Evidence features, as SQL predicates over the per-transaction view built below.
# Each is a yes/no question the agent can actually answer from a graph tool.
FEATURES: dict[str, str] = {
    # the model's own opinion, bucketed so we can see where it is worth anything
    "risk_00_30": "risk_score < 0.30",
    "risk_30_50": "risk_score >= 0.30 AND risk_score < 0.50",
    "risk_50_70": "risk_score >= 0.50 AND risk_score < 0.70",
    "risk_70_85": "risk_score >= 0.70 AND risk_score < 0.85",
    "risk_85_100": "risk_score >= 0.85",

    # channel and device
    "channel_online": "channel = 'online'",
    "device_new": "device_new = 'New'",
    "device_found": "device_new = 'Found'",
    "proxy_present": "proxy_flag <> ''",
    "device_never_used_on_card": "device_key <> '' AND prior_same_device = 0",
    "device_profile_generic": "device_n_cards > 20",
    "match_status_mismatch": "match_status LIKE '%match_status:0%'",

    # amount, always relative to the card's own history
    "amt_above_prior_p95ish": "prior_on_card >= 10 AND amt > prior_mean + 2 * prior_std",
    "amt_below_prior_mean": "prior_on_card >= 10 AND amt < prior_mean",
    "amt_seen_before_on_card": "prior_same_amt >= 1",
    "amt_over_500": "amt > 500",
    "amt_under_5": "amt < 5",

    # geography, evaluated as of the transaction
    "region_novel": "addr1 <> '' AND prior_in_region = 0 AND prior_on_card >= 10",
    "region_well_established": "prior_in_region >= 10",
    "country_not_home": "addr2 <> '' AND addr2 <> '87.0'",
    "in_person_elsewhere_same_day": "elsewhere_same_day > 0",

    # product mix
    "product_novel_for_card": "prior_product = 0 AND prior_on_card >= 10",

    # timing and velocity
    "burst_under_1h": "secs_since_prev >= 0 AND secs_since_prev < 3600",
    "burst_under_10m": "secs_since_prev >= 0 AND secs_since_prev < 600",
    "first_txn_on_card": "prior_on_card = 0",

    # match flags (M1..M9), unnamed by Vesta; used as signals, labelled honestly
    "m_flags_any_false": "m_feats LIKE '%F%'",
    "m_flags_all_true": "m_feats NOT LIKE '%F%' AND m_feats <> ''",

    # distance feature, meaning unknown; treated as a signal, not a fact
    "dist1_missing": "dist1 < 0",
    "dist1_large": "dist1 > 100",
}

# Features that restate the same underlying observation. The ledger caps each
# group so three phrasings of "new device" cannot triple-count.
GROUPS: dict[str, str] = {
    "risk_00_30": "risk_score", "risk_30_50": "risk_score", "risk_50_70": "risk_score",
    "risk_70_85": "risk_score", "risk_85_100": "risk_score",
    "device_new": "device", "device_found": "device", "proxy_present": "device",
    "device_never_used_on_card": "device", "device_profile_generic": "device",
    "amt_above_prior_p95ish": "amount", "amt_below_prior_mean": "amount",
    "amt_seen_before_on_card": "amount", "amt_over_500": "amount", "amt_under_5": "amount",
    "region_novel": "region", "region_well_established": "region",
    "country_not_home": "region", "in_person_elsewhere_same_day": "region",
    "burst_under_1h": "velocity", "burst_under_10m": "velocity",
    "m_flags_any_false": "match_flags", "m_flags_all_true": "match_flags",
    "dist1_missing": "distance", "dist1_large": "distance",
}

MIN_SUPPORT = 20      # below this, an LR is shrunk hard toward 1
SHRINK_STRENGTH = 30  # pseudo-counts pulling each rate toward the pooled rate


def build_view(con: duckdb.DuckDBPyConnection) -> None:
    """One pass over the staged transactions, computing as-of features."""
    print("building per-transaction features (as of each transaction)")
    con.execute(
        "CREATE VIEW raw AS SELECT * FROM read_csv('"
        + (cfg.STAGING / "transactions.csv").as_posix()
        + "', all_varchar=true, sample_size=-1)"
    )
    con.execute(
        "CREATE VIEW dev AS SELECT device_key, CAST(n_cards AS BIGINT) AS device_n_cards "
        "FROM read_csv('" + (cfg.STAGING / "devices.csv").as_posix() + "', all_varchar=true)"
    )
    con.execute(
        """
        CREATE TABLE feat AS
        WITH t AS (
          SELECT txn_id, card_id, customer_id, CAST(ts AS TIMESTAMP) AS ts,
                 CAST(amt AS DOUBLE) AS amt, product, channel,
                 CAST(risk_score AS DOUBLE) AS risk_score,
                 addr1, addr2, CAST(dist1 AS DOUBLE) AS dist1,
                 device_key, device_new,
                 proxy AS proxy_flag,
                 match_status, m_feats
          FROM raw
        ), w AS (
          SELECT *,
            count(*) OVER pc  AS prior_on_card,
            count(*) OVER prg AS prior_in_region,
            count(*) OVER ppd AS prior_product,
            count(*) OVER pam AS prior_same_amt,
            count(*) OVER pdv AS prior_same_device,
            avg(amt) OVER pc  AS prior_mean,
            coalesce(stddev_samp(amt) OVER pc, 0) AS prior_std,
            date_diff('second', lag(ts) OVER (PARTITION BY card_id ORDER BY ts), ts)
              AS secs_since_prev
          FROM t
          WINDOW
            pc  AS (PARTITION BY card_id ORDER BY ts
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
            prg AS (PARTITION BY card_id, addr1 ORDER BY ts
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
            ppd AS (PARTITION BY card_id, product ORDER BY ts
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
            pam AS (PARTITION BY card_id, round(amt) ORDER BY ts
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
            pdv AS (PARTITION BY card_id, device_key ORDER BY ts
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
        ), same_day AS (
          -- Card-present activity in a DIFFERENT region on the same calendar day:
          -- the trip-versus-clone discriminator. A cardholder cannot be in two
          -- billing regions at once; a cloned card can.
          SELECT a.txn_id, count(*) AS elsewhere_same_day
          FROM w a JOIN w b
            ON b.card_id = a.card_id
           AND b.ts::DATE = a.ts::DATE
           AND b.addr1 <> a.addr1
           AND b.addr1 <> ''
           AND b.channel = 'in_person'
          WHERE a.channel = 'in_person' AND a.addr1 <> ''
          GROUP BY a.txn_id
        )
        SELECT w.*,
               coalesce(s.elsewhere_same_day, 0) AS elsewhere_same_day,
               coalesce(d.device_n_cards, 0) AS device_n_cards
        FROM w
        LEFT JOIN same_day s ON s.txn_id = w.txn_id
        LEFT JOIN dev d ON d.device_key = w.device_key
        """
    )
    n = con.execute("SELECT count(*) FROM feat").fetchone()[0]
    print(f"  {n:,} transactions with as-of features")


def build_labels(con: duckdb.DuckDBPyConnection) -> tuple[int, int, int]:
    """Positives, matched controls, and the cleared alerts kept for reference."""
    con.execute(
        "CREATE VIEW cc AS SELECT * FROM read_csv('"
        + cfg.RAW_CLOSED_CASES.as_posix()
        + "', all_varchar=true, sample_size=-1)"
    )
    con.execute(
        """
        CREATE TABLE case_txn AS
        SELECT trim(u.x) AS txn_id, outcome
        FROM cc, UNNEST(string_split(coalesce(txn_ids, ''), '|')) u(x)
        WHERE trim(u.x) <> ''
        """
    )
    con.execute(
        "CREATE TABLE fraud_cards AS "
        "SELECT DISTINCT card_id FROM cc WHERE outcome = 'confirmed_fraud'"
    )
    con.execute(
        """
        CREATE TABLE fit AS
        SELECT f.*, 1 AS is_fraud FROM feat f
          JOIN case_txn ct ON ct.txn_id = f.txn_id AND ct.outcome = 'confirmed_fraud'
        UNION ALL
        SELECT f.*, 0 AS is_fraud FROM feat f
          JOIN fraud_cards fc ON fc.card_id = f.card_id
          LEFT JOIN case_txn ct ON ct.txn_id = f.txn_id
         WHERE ct.txn_id IS NULL
           AND f.ts >= TIMESTAMP '2016-07-02' AND f.ts < TIMESTAMP '2016-11-03'
        """
    )
    con.execute(
        """
        CREATE TABLE cleared AS
        SELECT f.* FROM feat f
          JOIN case_txn ct ON ct.txn_id = f.txn_id AND ct.outcome = 'cleared'
        """
    )
    pos = con.execute("SELECT count(*) FROM fit WHERE is_fraud = 1").fetchone()[0]
    neg = con.execute("SELECT count(*) FROM fit WHERE is_fraud = 0").fetchone()[0]
    clr = con.execute("SELECT count(*) FROM cleared").fetchone()[0]
    cards = con.execute("SELECT count(*) FROM fraud_cards").fetchone()[0]
    print(f"  positives: {pos:,} confirmed-fraud transactions")
    print(f"  controls:  {neg:,} other transactions on the same {cards:,} cards")
    print(f"  reference: {clr:,} cleared-alert transactions (not used for the LRs)")
    return pos, neg, clr


def trigger_priors(con: duckdb.DuckDBPyConnection) -> dict:
    """Base rates by trigger pathway, with the degeneracy stated plainly."""
    rows = con.execute(
        """
        SELECT CASE WHEN analyst_notes LIKE '%model scored%' THEN 'risk_score'
                    WHEN analyst_notes LIKE '%reported unrecognized%' THEN 'customer_report'
                    ELSE 'other' END AS trigger,
               outcome, count(*) AS n
        FROM cc GROUP BY 1, 2
        """
    ).fetchall()
    tally: dict[str, dict[str, int]] = {}
    for trigger, outcome, n in rows:
        tally.setdefault(trigger, {})[outcome] = n

    out: dict[str, dict] = {}
    for trigger, counts in tally.items():
        fraud = counts.get("confirmed_fraud", 0)
        clear = counts.get("cleared", 0)
        total = fraud + clear
        raw = fraud / total if total else 0.5
        # The historical rates are exactly 1.0 and 0.0. Using them would make the
        # agent unable ever to clear a customer report or ever to confirm a
        # score-triggered alert, which contradicts policy R7 (disputed but
        # legitimate) and the README ("some fraud scores near zero"). They are
        # shrunk halfway toward the rate the README implies for the exam pack
        # (about half of the 20 cases legitimate). A judgement call, recorded
        # here rather than buried in a prompt.
        shrunk = 0.5 + (raw - 0.5) * 0.5
        out[trigger] = {
            "n_confirmed_fraud": fraud,
            "n_cleared": clear,
            "historical_rate": round(raw, 4),
            "prior_used": round(shrunk, 4),
            "note": "historical rate is degenerate (see module docstring); "
                    "shrunk 50 percent toward 0.5",
        }
    return out


def wilson_shrink(k: int, n: int, pooled: float) -> float:
    """Rate with pseudo-counts pulling toward the pooled rate when support is thin."""
    if n == 0:
        return pooled
    return (k + SHRINK_STRENGTH * pooled) / (n + SHRINK_STRENGTH)


def fit(con: duckdb.DuckDBPyConnection, n_pos: int, n_neg: int, n_clr: int) -> dict:
    table: dict[str, dict] = {}
    for name, predicate in FEATURES.items():
        k_pos, k_neg = con.execute(
            "SELECT sum(CASE WHEN is_fraud = 1 AND (" + predicate + ") THEN 1 ELSE 0 END), "
            "       sum(CASE WHEN is_fraud = 0 AND (" + predicate + ") THEN 1 ELSE 0 END) "
            "FROM fit"
        ).fetchone()
        k_pos, k_neg = int(k_pos or 0), int(k_neg or 0)
        k_clr = int(con.execute(
            "SELECT sum(CASE WHEN (" + predicate + ") THEN 1 ELSE 0 END) FROM cleared"
        ).fetchone()[0] or 0)

        pooled = (k_pos + k_neg) / max(n_pos + n_neg, 1)
        p_fraud = min(max(wilson_shrink(k_pos, n_pos, pooled), 1e-4), 1 - 1e-4)
        p_legit = min(max(wilson_shrink(k_neg, n_neg, pooled), 1e-4), 1 - 1e-4)

        lr_present = p_fraud / p_legit
        lr_absent = (1 - p_fraud) / (1 - p_legit)

        support = min(k_pos + k_neg, n_pos + n_neg - k_pos - k_neg)
        thin = support < MIN_SUPPORT
        if thin:
            lr_present = 1 + (lr_present - 1) * support / MIN_SUPPORT
            lr_absent = 1 + (lr_absent - 1) * support / MIN_SUPPORT

        table[name] = {
            "predicate": predicate,
            "group": GROUPS.get(name, name),
            "n_fraud_with": k_pos,
            "n_control_with": k_neg,
            "p_given_fraud": round(p_fraud, 5),
            "p_given_control": round(p_legit, 5),
            "p_given_cleared_alert": round(k_clr / n_clr, 5) if n_clr else None,
            "lr_present": round(lr_present, 4),
            "lr_absent": round(lr_absent, 4),
            "log_lr_present": round(math.log(lr_present), 4),
            "thin_support": thin,
        }
    return table


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    con = duckdb.connect()
    con.execute("PRAGMA enable_progress_bar=false")
    build_view(con)
    n_pos, n_neg, n_clr = build_labels(con)
    priors = trigger_priors(con)
    table = fit(con, n_pos, n_neg, n_clr)

    payload = {
        "design": {
            "positives": f"{n_pos} transactions from confirmed_fraud closed cases",
            "negatives": f"{n_neg} other transactions on the same cards, outside any "
                         f"case, July-October 2016 (case-control matched on card)",
            "why_matched": "Every cleared case in this dataset was model-triggered and "
                           "every confirmed case was customer-triggered, so fitting "
                           "fraud-vs-cleared measures trigger pathway, not fraud. "
                           "Matching on card removes that confound and card-level "
                           "spending differences at the same time.",
            "reference_only": f"{n_clr} cleared-alert transactions, stored per feature "
                              f"as p_given_cleared_alert but not used in the ratios",
        },
        "trigger_priors": priors,
        "shrinkage": {"min_support": MIN_SUPPORT, "pseudo_counts": SHRINK_STRENGTH},
        "features": table,
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2) + NL, encoding="utf-8")
    print(NL + f"wrote {OUT_PATH.relative_to(cfg.ROOT)} with {len(table)} features")

    print(NL + "  trigger priors (historical rate -> prior actually used)")
    for trig, p in sorted(priors.items()):
        print(f"    {trig:<18}{p['n_confirmed_fraud']:>6} fraud /{p['n_cleared']:>5} cleared"
              f"   {p['historical_rate']:.2f} -> {p['prior_used']:.2f}")

    ranked = sorted(table.items(), key=lambda kv: kv[1]["lr_present"], reverse=True)
    print(NL + "  strongest evidence FOR fraud (vs other transactions on the same card)")
    for name, f in ranked[:8]:
        flag = " (thin)" if f["thin_support"] else ""
        print(f"    LR {f['lr_present']:>6.2f}  {name:<30}"
              f"{f['p_given_fraud']:>7.1%} fraud vs{f['p_given_control']:>7.1%} control{flag}")
    print(NL + "  strongest evidence AGAINST fraud")
    for name, f in ranked[-8:][::-1]:
        flag = " (thin)" if f["thin_support"] else ""
        print(f"    LR {f['lr_present']:>6.2f}  {name:<30}"
              f"{f['p_given_fraud']:>7.1%} fraud vs{f['p_given_control']:>7.1%} control{flag}")

    if args.report:
        print(NL + "  full table")
        for name, f in sorted(table.items()):
            print(f"    {name:<32} LR={f['lr_present']:>7.3f}  "
                  f"fraud={f['n_fraud_with']:>6}  control={f['n_control_with']:>6}")


if __name__ == "__main__":
    main()
