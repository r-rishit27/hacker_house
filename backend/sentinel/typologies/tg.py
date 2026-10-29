"""A :class:`TxnSource` over TigerGraph: the graph is the source of truth.

Every question the detectors and the decision layer ask becomes one of the
installed v3 queries (``graph/queries/sentinel_queries_v3.gsql``). The DuckDB
lab is kept only as a backtest mirror; ``eval/graph_parity.py`` proves the two
give identical decisions.

Fail closed: any graph error raises :class:`GraphUnavailable`. The runner then
reports the case as failed and writes nothing — it never falls back to the
local mirror and presents the answer as if the graph had produced it.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from functools import lru_cache

from sentinel.typologies.model import Txn

TS = "%Y-%m-%d %H:%M:%S"


class GraphUnavailable(RuntimeError):
    """TigerGraph could not answer; the investigation must stop, not guess."""


def _none(v):
    return None if v in ("", None, "-1.0") else v


def _txn(a: dict) -> Txn:
    return Txn(
        txn_id=int(a["txn_id"]), card_id=a["card_id"], customer_id=a["customer_id"],
        ts=datetime.strptime(a["ts"], TS), amt=float(a["amt"]), product=a["product"], channel=a["channel"],
        addr1=_none(a.get("addr1")), p_email=_none(a.get("p_email")), r_email=_none(a.get("r_email")),
        device_key=_none(a.get("device_key")), device_new=_none(a.get("device_new")),
        proxy=_none(a.get("proxy_flag")), risk_score=float(a["risk_score"]) if a.get("risk_score") is not None else None,
    )


class TigerGraphTxnSource:
    def __init__(self, conn=None) -> None:
        if conn is None:
            import sys
            from pathlib import Path

            sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
            from etl import config as cfg

            try:
                conn = cfg.connect(verbose=False)
            except Exception as exc:
                raise GraphUnavailable(f"cannot connect to TigerGraph: {type(exc).__name__}: {exc}") from exc
        self.conn = conn
        self.calls = 0
        self.label = f"TigerGraph {getattr(conn, 'graphname', 'GRAPH_GOA')} (v3 queries ty_*)"
        self.data_start = datetime.fromisoformat(os.getenv("DATA_START", "2016-07-02 00:02:21"))

    # -- transport ----------------------------------------------------------
    def _q(self, name: str, params: dict, key: str) -> list[dict]:
        self.calls += 1
        try:
            res = self.conn.runInstalledQuery(name, params=params, timeout=120_000)
        except Exception as exc:
            raise GraphUnavailable(f"query {name} failed: {type(exc).__name__}: {str(exc)[:200]}") from exc
        for block in res:
            if key in block:
                # PRINT V[V.attr, ...] returns keys as "V.attr"; strip the alias.
                return [{k.split(".", 1)[-1]: val for k, val in v["attributes"].items()} for v in block[key]]
        raise GraphUnavailable(f"query {name} returned no '{key}' block")

    @staticmethod
    def _t(d: datetime) -> str:
        return d.strftime(TS)

    def _txns(self, name: str, params: dict) -> list[Txn]:
        return sorted((_txn(a) for a in self._q(name, params, "T")), key=lambda t: (t.ts, t.txn_id))

    # -- TxnSource ------------------------------------------------------------
    @lru_cache(maxsize=2048)
    def card_txns(self, card_id: str, until: datetime) -> list[Txn]:
        return self._txns("ty_card_txns", {"c_in": (card_id,), "until": self._t(until)})

    @lru_cache(maxsize=2048)
    def txns_by_device(self, device_key: str, since: datetime, until: datetime) -> list[Txn]:
        return self._txns("ty_device_txns", {"d_in": (device_key,), "since": self._t(since), "until": self._t(until)})

    @lru_cache(maxsize=2048)
    def txns_by_recipient(self, r_email: str, since: datetime, until: datetime) -> list[Txn]:
        return self._txns("ty_recipient_txns", {"e_in": (r_email,), "since": self._t(since), "until": self._t(until)})

    @lru_cache(maxsize=512)
    def txns_in_band(self, lo: float, hi: float, since: datetime, until: datetime) -> list[Txn]:
        return self._txns("ty_band_txns", {"lo": lo, "hi": hi, "since": self._t(since), "until": self._t(until)})

    @lru_cache(maxsize=4096)
    def device_card_count(self, device_key: str, until: datetime) -> int:
        return len({t.card_id for t in self.txns_by_device(device_key, self.data_start, until)})

    def txn(self, txn_id: int) -> Txn | None:
        self.calls += 1
        try:
            rows = self.conn.getVerticesById("Transaction", [str(txn_id)])
        except Exception as exc:
            raise GraphUnavailable(f"txn lookup failed: {exc}") from exc
        return _txn(rows[0]["attributes"]) if rows else None

    # -- precedent ------------------------------------------------------------
    def prior_cases(self, card: str, before: datetime) -> list[dict]:
        rows = self._q("ty_card_prior_cases", {"c_in": (card,), "before": self._t(before)}, "C")
        rows.sort(key=lambda c: (c["opened_at"], c["case_id"]))
        keys = ("case_id", "card_id", "opened_at", "outcome", "pattern", "exposure_usd")
        return [{k: c[k] for k in keys} for c in rows][-8:]

    CLEARED_PHRASES = {
        "device_new": ["%new phone%", "%Device marked New%"],
        "new_device_behind_proxy": ["%proxy%"],
        "region_new": ["%travel%"],
        "amount_high": ["%far above%"],
        "late_night": ["%Late-night%"],
        "proxy": ["%proxy%"],
    }

    def _cases_on_cards(self, cards: set[str], before: datetime) -> list[dict]:
        if not cards:
            return []
        return self._q("ty_cards_closed_cases", {"cards": [(c,) for c in sorted(cards)], "before": self._t(before)}, "C")

    def linked_closed_cases(self, card_id: str, devices: list[str], recipients: list[str],
                            before: datetime, limit: int = 8) -> list[dict]:
        via: dict[str, str] = {card_id: "same card"}
        for dk in devices:
            for t in self.txns_by_device(dk, self.data_start, before):
                via.setdefault(t.card_id, f"device {dk}")
        for r in recipients:
            for t in self.txns_by_recipient(r, self.data_start, before):
                via.setdefault(t.card_id, f"recipient {r}")
        rows = self._cases_on_cards(set(via), before)
        out = [{"case_id": c["case_id"], "card_id": c["card_id"], "linked_via": via.get(c["card_id"], ""),
                "opened_at": c["opened_at"], "outcome": c["outcome"], "pattern": c["pattern"],
                "exposure_usd": c["exposure_usd"], "analyst_notes": c["analyst_notes"][:320]} for c in rows]
        out.sort(key=lambda c: (c["linked_via"] != "same card", c["opened_at"]), reverse=False)
        same = [c for c in out if c["linked_via"] == "same card"]
        other = sorted((c for c in out if c["linked_via"] != "same card"), key=lambda c: c["opened_at"], reverse=True)
        same.sort(key=lambda c: c["opened_at"], reverse=True)
        return (same + other)[:limit]

    def cleared_lookalikes(self, anomaly_keys: list[str], before: datetime, per_key: int = 2) -> list[dict]:
        out = []
        for k in anomaly_keys:
            seen = 0
            for phrase in self.CLEARED_PHRASES.get(k, []):
                for c in self._q("ty_cleared_like", {"phrase": phrase, "before": self._t(before), "k": per_key}, "C"):
                    if seen < per_key:
                        out.append({"anomaly": k, "case_id": c["case_id"], "opened_at": c["opened_at"],
                                    "outcome": "cleared", "analyst_notes": c["analyst_notes"][:260]})
                        seen += 1
        return out

    @lru_cache(maxsize=1)
    def history_context(self) -> dict:
        self.calls += 1
        try:
            cleared = self.conn.getVertexCount("ClosedCase", where='outcome="cleared"')
            fraud = self.conn.getVertexCount("ClosedCase", where='outcome="confirmed_fraud"')
        except Exception as exc:
            raise GraphUnavailable(f"closed-case counts failed: {exc}") from exc
        travel = len(self._q("ty_cleared_like", {"phrase": "%travel%", "before": "2100-01-01 00:00:00", "k": 100000}, "C"))
        phone = len({c["case_id"] for p in ("%new phone%", "%Device marked New%")
                     for c in self._q("ty_cleared_like", {"phrase": p, "before": "2100-01-01 00:00:00", "k": 100000}, "C")})
        return {
            "closed_cases": cleared + fraud, "cleared": cleared, "confirmed_fraud": fraud,
            "cleared_as_confirmed_travel": travel, "cleared_as_new_device_confirmed_by_cardholder": phone,
            "note": "Alerts raised by the risk model alone were overwhelmingly cleared by analysts; cases opened by "
                    "a customer report were overwhelmingly confirmed fraud. A new device or a new region, on its "
                    "own, is the most common reason a model alert turned out legitimate.",
        }
