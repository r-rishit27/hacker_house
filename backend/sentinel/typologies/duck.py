"""A :class:`TxnSource` over the local DuckDB lab (``data/lab.duckdb``).

Used by the backtest and by offline runs. The live agent reads the same rows
from TigerGraph; the detectors cannot tell the difference.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path

import duckdb

from sentinel.typologies.model import Txn

_COLS = "txn_id, card_id, customer_id, ts, amt, product, channel, addr1, p_email, r_email, device_key, device_new, proxy, risk_score"


class DuckTxnSource:
    def __init__(self, path: str | Path, *, read_only: bool = True) -> None:
        self.con = duckdb.connect(str(path), read_only=read_only)
        self.calls = 0
        self.recorder = None
        #: Named in every evidence ref, so a judge knows which store produced the claim.
        self.label = "lab.duckdb mirror (Jul 2016 - Jan 2017, closed_cases_history 5,585)"
        #: The earliest transaction: no "before" window may reach past it.
        self.data_start: datetime = self.con.execute("SELECT min(ts) FROM txn").fetchone()[0]

    def _rows(self, where: str, params: list) -> list[Txn]:
        self.calls += 1
        rows = [Txn(*r) for r in self.con.execute(f"SELECT {_COLS} FROM txn WHERE {where} ORDER BY ts, txn_id", params).fetchall()]
        if self.recorder:
            self.recorder(f"lab:txn WHERE {where}", {f"p{i}": v for i, v in enumerate(params)}, rows)
        return rows

    def card_txns(self, card_id: str, until: datetime) -> list[Txn]:
        return self._rows("card_id = ? AND ts < ?", [card_id, until])

    def txns_by_device(self, device_key: str, since: datetime, until: datetime) -> list[Txn]:
        return self._rows("device_key = ? AND ts >= ? AND ts < ?", [device_key, since, until])

    def txns_by_recipient(self, r_email: str, since: datetime, until: datetime) -> list[Txn]:
        return self._rows("r_email = ? AND ts >= ? AND ts < ?", [r_email, since, until])

    def txns_in_band(self, lo: float, hi: float, since: datetime, until: datetime) -> list[Txn]:
        return self._rows("amt >= ? AND amt < ? AND ts >= ? AND ts < ?", [lo, hi, since, until])

    @lru_cache(maxsize=4096)
    def device_card_count(self, device_key: str, until: datetime) -> int:
        self.calls += 1
        return self.con.execute(
            "SELECT count(DISTINCT card_id) FROM txn WHERE device_key = ? AND ts < ?", [device_key, until]
        ).fetchone()[0]

    def linked_closed_cases(self, card_id: str, devices: list[str], recipients: list[str],
                            before: datetime, limit: int = 8) -> list[dict]:
        """Precedent by shared entity: closed cases on this card, on cards that used the
        same device profiles, or on cards that paid the same recipients — closed before as-of."""
        self.calls += 1
        rows = self.con.execute(
            """
            WITH linked AS (
              SELECT ?::VARCHAR AS card_id, 'same card' AS via
              UNION SELECT DISTINCT card_id, 'device ' || device_key FROM txn
                    WHERE device_key IN (SELECT unnest(?::VARCHAR[])) AND ts < ? AND card_id <> ?
              UNION SELECT DISTINCT card_id, 'recipient ' || r_email FROM txn
                    WHERE r_email IN (SELECT unnest(?::VARCHAR[])) AND ts < ? AND card_id <> ?)
            SELECT c.case_id, c.card_id, any_value(l.via), c.opened_at, c.outcome, c.pattern, c.exposure_usd,
                   left(c.analyst_notes, 320)
            FROM closed_cases c JOIN linked l ON l.card_id = c.card_id
            WHERE CAST(c.closed_at AS TIMESTAMP) < ?
            GROUP BY ALL ORDER BY (any_value(l.via) = 'same card') DESC, c.opened_at DESC LIMIT ?
            """,
            [card_id, devices, before, card_id, recipients, before, card_id, before, limit],
        ).fetchall()
        keys = ("case_id", "card_id", "linked_via", "opened_at", "outcome", "pattern", "exposure_usd", "analyst_notes")
        return [dict(zip(keys, r)) for r in rows]

    #: anomaly key -> the phrase the bank's analysts use when they clear that look-alike
    CLEARED_PHRASES = {
        "device_new": "new phone|Device marked New",
        "new_device_behind_proxy": "proxy",
        "region_new": "travel",
        "amount_high": "far above",
        "late_night": "Late-night",
        "proxy": "proxy",
        "prior_fraud": "closed fraud case",
    }

    def cleared_lookalikes(self, anomaly_keys: list[str], before: datetime, per_key: int = 2) -> list[dict]:
        """Closed cases the bank *cleared* that had the same kind of anomaly — the
        precedent that says "this signal alone was not fraud last time"."""
        out = []
        for k in anomaly_keys:
            phrase = self.CLEARED_PHRASES.get(k)
            if not phrase:
                continue
            self.calls += 1
            rows = self.con.execute(
                "SELECT case_id, opened_at, left(analyst_notes, 260) FROM closed_cases WHERE outcome = 'cleared' "
                "AND regexp_matches(analyst_notes, ?) AND CAST(closed_at AS TIMESTAMP) < ? ORDER BY opened_at DESC LIMIT ?",
                [phrase, before, per_key],
            ).fetchall()
            out += [{"anomaly": k, "case_id": r[0], "opened_at": r[1], "outcome": "cleared", "analyst_notes": r[2]}
                    for r in rows]
        return out

    @lru_cache(maxsize=1)
    def history_context(self) -> dict:
        """Base rates from the closed-case history, stated rather than left for the model to guess."""
        self.calls += 1
        n_cleared, n_fraud = self.con.execute(
            "SELECT count(*) FILTER (WHERE outcome='cleared'), count(*) FILTER (WHERE outcome='confirmed_fraud') "
            "FROM closed_cases").fetchone()
        travel, phone = self.con.execute(
            "SELECT count(*) FILTER (WHERE analyst_notes ILIKE '%travel%'), "
            "count(*) FILTER (WHERE analyst_notes ILIKE '%new phone%' OR analyst_notes ILIKE '%Device marked New%') "
            "FROM closed_cases WHERE outcome='cleared'").fetchone()
        return {
            "closed_cases": n_cleared + n_fraud, "cleared": n_cleared, "confirmed_fraud": n_fraud,
            "cleared_as_confirmed_travel": travel, "cleared_as_new_device_confirmed_by_cardholder": phone,
            "note": "Alerts raised by the risk model alone were overwhelmingly cleared by analysts; cases opened by "
                    "a customer report were overwhelmingly confirmed fraud. A new device or a new region, on its "
                    "own, is the most common reason a model alert turned out legitimate.",
        }

    def prior_cases(self, card: str, before: datetime) -> list[dict]:
        """Closed cases on this card, or naming it as connected, closed before as-of."""
        self.calls += 1
        rows = self.con.execute(
            "SELECT case_id, card_id, opened_at, outcome, pattern, exposure_usd FROM closed_cases "
            "WHERE (card_id = ? OR case_id IN (SELECT case_id FROM cc_conn WHERE card_id = ?)) AND closed_at < ? "
            "ORDER BY opened_at",
            [card, card, before.isoformat(sep=" ")],
        ).fetchall()
        keys = ("case_id", "card_id", "opened_at", "outcome", "pattern", "exposure_usd")
        return [dict(zip(keys, r)) for r in rows][-8:]

    @lru_cache(maxsize=4096)
    def device_fraud_history(self, device_key: str, before: datetime) -> dict:
        """How many cards used this device before as-of, and how many of them the bank tied
        to confirmed fraud through it: the card of a confirmed case whose transactions used
        the device, or a card that case named as connected (that is how rings are recorded)."""
        self.calls += 1
        n_cards, n_fraud = self.con.execute(
            """WITH dev AS (SELECT DISTINCT card_id FROM txn WHERE device_key = ? AND ts < ?),
                    cases AS (SELECT DISTINCT c.case_id, c.card_id FROM closed_cases c
                              JOIN cc_txn x ON x.case_id = c.case_id JOIN txn t ON t.txn_id = x.txn_id
                              WHERE t.device_key = ? AND c.outcome = 'confirmed_fraud'
                                AND CAST(c.closed_at AS TIMESTAMP) < ?),
                    tied AS (SELECT card_id FROM cases UNION
                             SELECT k.card_id FROM cc_conn k JOIN cases USING (case_id))
               SELECT (SELECT count(*) FROM dev), (SELECT count(*) FROM dev WHERE card_id IN (SELECT card_id FROM tied))""",
            [device_key, before, device_key, before]).fetchone()
        return {"n_cards": n_cards, "n_fraud_cards": n_fraud}

    def txn(self, txn_id: int) -> Txn | None:
        r = self._rows("txn_id = ?", [txn_id])
        return r[0] if r else None
