"""Any mapped bank graph, read as the transaction rows the detectors already use.

One class serves every bank. It needs four things from the store — look up a
vertex, follow an edge, scan a vertex type, read an attribute — so it runs on
TigerGraph's built-in REST endpoints with nothing installed per bank, or on an
in-memory graph for tests and onboarding dry runs.

How a canonical row is built from a bank's records, all driven by the mapping:

    txn_id      the Transaction vertex id (any of the vertex types mapped to Transaction)
    card_id     the debited Account, reached backwards along INITIATED
    customer_id the Person, reached backwards along OWNS
    ts, amt     Transaction.timestamp / .amount, with the mapped transform (YYMMDDHHMM strings...)
    channel     Transaction.channel through the reviewed value table, or the bank's default
    r_email     the receiving Beneficiary or Account (PAYS / PAYS_TO_ACCOUNT), as "Type:id"
    device_key  the Device along FROM_DEVICE (a vertex, or an attribute of the vertex reached)
    proxy       "IP_PROXY:ANONYMOUS" when the IP reached along FROM_IP is marked as an anonymizer

Nothing is invented: a canonical field with no mapping stays empty, and the
capability report says which detectors that switches off.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sentinel.onboarding.canonical import RELATIONS
from sentinel.onboarding.schema import Schema
from sentinel.typologies.model import Txn


class GraphReader(Protocol):
    def vertex(self, vtype: str, vid: str) -> dict | None: ...

    def neighbors(self, vtype: str, vid: str, edge: str) -> list[tuple[str, str]]: ...

    def scan(self, vtype: str) -> Iterable[tuple[str, dict]]: ...


# ── readers ──────────────────────────────────────────────────────────────────

class InMemoryGraph:
    """A bank's records in its own schema: {"vertices": {type: {id: attrs}}, "edges": [[edge, ftype, fid, ttype, tid]]}."""

    def __init__(self, schema: Schema, data: dict) -> None:
        self.schema = schema
        self.v = {t: {str(k): a for k, a in rows.items()} for t, rows in data.get("vertices", {}).items()}
        self.adj: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
        for edge, ft, fid, tt, tid in data.get("edges", []):
            e = schema.edges[edge]
            self.adj.setdefault((ft, str(fid), edge), []).append((tt, str(tid)))
            back = edge if not e.directed else e.reverse
            if back:
                self.adj.setdefault((tt, str(tid), back), []).append((ft, str(fid)))

    def vertex(self, vtype, vid):
        return self.v.get(vtype, {}).get(str(vid))

    def neighbors(self, vtype, vid, edge):
        return self.adj.get((vtype, str(vid), edge), [])

    def scan(self, vtype):
        return self.v.get(vtype, {}).items()


class TigerGraphReader:
    """The same four calls over TigerGraph's built-in REST endpoints: no installed query per bank."""

    #: TigerGraph answers "no such vertex" with an error rather than an empty result; only that becomes "not found".
    NOT_FOUND = ("not a valid vertex id", "does not exist", "is not found")

    def __init__(self, conn) -> None:
        self.conn = conn

    def _absent(self, exc: Exception) -> bool:
        return any(s in str(exc) for s in self.NOT_FOUND)

    def vertex(self, vtype, vid):
        try:
            rows = self.conn.getVerticesById(vtype, [str(vid)])
        except Exception as exc:  # a network or auth failure is re-raised; the case fails closed
            if self._absent(exc):
                return None
            raise
        return rows[0]["attributes"] if rows else None

    def neighbors(self, vtype, vid, edge):
        try:
            edges = self.conn.getEdges(vtype, str(vid), edge)
        except Exception as exc:
            if self._absent(exc):
                return []
            raise
        return [(e["to_type"], str(e["to_id"])) for e in edges]

    def scan(self, vtype):
        return ((str(r["v_id"]), r["attributes"]) for r in self.conn.getVertices(vtype, limit=200_000))


# ── values ───────────────────────────────────────────────────────────────────

def convert(value, transform: str | None):
    """A raw attribute value in the canonical form its transform names."""
    if value in (None, ""):
        return None
    t = transform or "none"
    s = str(value).strip()
    try:
        if t in ("none", "code"):
            return value
        if t == "datetime":
            return value if isinstance(value, datetime) else datetime.fromisoformat(s.replace("Z", ""))
        if t == "yyyymmdd":
            return datetime.strptime(s[:8], "%Y%m%d")
        if t == "yymmddhhmm":
            return datetime.strptime(s[:10], "%y%m%d%H%M")
        if t == "yyyymmddhhmmss":
            return datetime.strptime(s[:14], "%Y%m%d%H%M%S")
        if t == "epoch_s":
            return datetime.fromtimestamp(float(s))
        if t == "epoch_ms":
            return datetime.fromtimestamp(float(s) / 1000)
        if t == "yes_no":
            return s.upper() in ("Y", "YES", "TRUE", "1")
    except (ValueError, OverflowError):
        return None
    return value


def _passes(attrs: dict | None, f: dict | None) -> bool:
    if not f:
        return True
    if attrs is None:
        return False
    v = str(attrs.get(f["attribute"])).strip().lower()
    vals = [str(x).strip().lower() for x in f.get("values", [])]
    op = f.get("op", "=")
    if op in ("=", "in"):
        return v in vals
    if op in ("!=", "not_in"):
        return v not in vals
    try:
        return float(v) > float(vals[0]) if op == ">" else float(v) < float(vals[0])
    except (ValueError, IndexError):
        return False


def invert(schema: Schema, step: str) -> str:
    """The edge name that walks ``step`` the other way."""
    if step in schema.edges:
        e = schema.edges[step]
        return e.reverse if e.directed else step
    fwd = next((e.name for e in schema.edges.values() if e.reverse == step), None)
    if fwd is None:
        raise KeyError(f"no edge named {step}")
    return fwd


# ── the mapped source ────────────────────────────────────────────────────────

@dataclass
class _Rel:
    start: str
    steps: list[str]
    end: str
    filter: dict | None


class MappedTxnSource:
    """``TxnSource`` for any bank whose mapping has been reviewed (sentinel/typologies/model.py)."""

    def __init__(self, schema: Schema, mapping: dict, reader: GraphReader, *, label: str | None = None) -> None:
        self.schema, self.m, self.g = schema, mapping, reader
        self.label = label or f"{mapping.get('bank', schema.graph)} via mapping"
        self.calls = 0
        self.data_start = None
        self.recorder = None
        self.defaults = mapping.get("defaults", {})
        self._txn_types = self._sources("Transaction")
        self._account_types = self._sources("Account")
        self._row_cache: dict[tuple[str, str], Txn | None] = {}

    # mapping helpers
    def _sources(self, concept: str) -> list[dict]:
        c = self.m["concepts"].get(concept) or {}
        return [s for s in c.get("sources", []) if c.get("status") != "unmapped"]

    def _paths(self, relation: str) -> list[_Rel]:
        r = self.m["relations"].get(relation) or {}
        if r.get("status") == "unmapped":
            return []
        return [_Rel(p["start"], list(p["steps"]), p["end"], p.get("filter")) for p in r.get("paths", [])]

    def _attr(self, target: str, vtype: str, attrs: dict | None):
        a = self.m["attributes"].get(target) or {}
        if a.get("status") == "unmapped" or attrs is None:
            return None
        for s in a.get("sources", []):
            if s["vertex"] == vtype and _passes(attrs, s.get("filter")):
                raw = attrs.get(s["attribute"])
                table = s.get("values")
                return table.get(str(raw), table.get("*")) if table else convert(raw, s.get("transform"))
        return None

    def _walk(self, vtype: str, vid: str, steps: list[str]) -> list[tuple[str, str]]:
        here = [(vtype, vid)]
        for edge in steps:
            nxt = []
            for t, i in here:
                self.calls += 1
                nxt += self.g.neighbors(t, i, edge)
            here = nxt
        return here

    def _follow(self, relation: str, vtype: str, vid: str) -> list[tuple[str, str]]:
        """Forward along a canonical relation from one vertex."""
        out = []
        for p in self._paths(relation):
            if p.start != vtype:
                continue
            for t, i in self._walk(vtype, vid, p.steps):
                if t != p.end:
                    continue
                if p.filter and not _passes(self.g.vertex(t, i), p.filter):
                    continue
                out.append((t, i))
        return out

    def _back(self, relation: str, vtype: str, vid: str) -> list[tuple[str, str]]:
        """Backwards along a canonical relation, to the vertex it starts from."""
        out = []
        for p in self._paths(relation):
            if p.end != vtype:
                continue
            if p.filter and not _passes(self.g.vertex(vtype, vid), p.filter):
                continue
            back = [invert(self.schema, s) for s in reversed(p.steps)]
            out += [(t, i) for t, i in self._walk(vtype, vid, back) if t == p.start]
        return out

    # canonical rows
    def _channel(self, vtype: str, attrs: dict) -> str:
        raw = self._attr("Transaction.channel", vtype, attrs)
        if raw is None:
            raw = self.defaults.get("Transaction.channel", {}).get(vtype) if isinstance(
                self.defaults.get("Transaction.channel"), dict) else self.defaults.get("Transaction.channel")
        return str(raw).lower() if raw is not None else "unknown"

    def _device(self, vtype: str, vid: str) -> str | None:
        dev = self.m["concepts"].get("Device") or {}
        for t, i in self._follow("FROM_DEVICE", vtype, vid):
            src = next((s for s in dev.get("sources", []) if s["vertex"] == t), None)
            if src and src.get("attribute"):
                val = (self.g.vertex(t, i) or {}).get(src["attribute"])
                if val:
                    return str(val)
            elif src:
                return i
        return None

    def _proxy(self, vtype: str, vid: str) -> str | None:
        for t, i in self._follow("FROM_IP", vtype, vid):
            flag = self._attr("IPAddress.anonymizer", t, self.g.vertex(t, i))
            if flag and str(flag).strip().lower() not in ("none", "false", "no", "0", "null"):
                return "IP_PROXY:ANONYMOUS"
        return None

    def row(self, vtype: str, vid: str, account: str | None = None) -> Txn | None:
        key = (vtype, vid)
        if key in self._row_cache and account is None:
            return self._row_cache[key]
        attrs = self.g.vertex(vtype, vid)
        src = next((s for s in self._txn_types if s["vertex"] == vtype), None)
        if attrs is None or src is None or not _passes(attrs, src.get("filter")):
            return None
        ts, amt = self._attr("Transaction.timestamp", vtype, attrs), self._attr("Transaction.amount", vtype, attrs)
        if not isinstance(ts, datetime) or amt is None:
            return None
        if account is None:
            accts = self._back("INITIATED", vtype, vid)
            if not accts:
                return None
            account = accts[0][1]
            acct_type = accts[0][0]
        else:
            acct_type = next((s["vertex"] for s in self._account_types if self.g.vertex(s["vertex"], account)), "")
        owners = self._back("OWNS", acct_type, account) if acct_type else []
        recipients = self._follow("PAYS", vtype, vid) or self._follow("PAYS_TO_ACCOUNT", vtype, vid)
        t = Txn(txn_id=vid, card_id=account, customer_id=owners[0][1] if owners else "", ts=ts, amt=float(amt),
                product=vtype, channel=self._channel(vtype, attrs),
                r_email=f"{recipients[0][0]}:{recipients[0][1]}" if recipients else None,
                device_key=self._device(vtype, vid), proxy=self._proxy(vtype, vid))
        self._row_cache[key] = t
        return t

    def _record(self, query: str, params: dict, rows: list) -> list:
        if self.recorder:
            self.recorder(query, params, rows)
        return rows

    # TxnSource
    def card_txns(self, card_id: str, until: datetime) -> list[Txn]:
        out = []
        for s in self._account_types:
            if self.g.vertex(s["vertex"], card_id) is None:
                continue
            for t, i in self._follow("INITIATED", s["vertex"], card_id):
                r = self.row(t, i, account=card_id)
                if r and r.ts < until:
                    out.append(r)
        out.sort(key=lambda r: (r.ts, str(r.txn_id)))
        return self._record("mapped.card_txns", {"account": card_id, "until": str(until)}, out)

    def _rows_from(self, pairs: Iterable[tuple[str, str]], since: datetime, until: datetime) -> list[Txn]:
        seen, out = set(), []
        for t, i in pairs:
            if (t, i) in seen:
                continue
            seen.add((t, i))
            r = self.row(t, i)
            if r and since <= r.ts < until:
                out.append(r)
        return sorted(out, key=lambda r: (r.ts, str(r.txn_id)))

    def txns_by_device(self, device_key: str, since: datetime, until: datetime) -> list[Txn]:
        dev = self.m["concepts"].get("Device") or {}
        pairs: list[tuple[str, str]] = []
        for s in dev.get("sources", []):
            holders = ([(s["vertex"], i) for i, a in self.g.scan(s["vertex"]) if str(a.get(s["attribute"])) == device_key]
                       if s.get("attribute") else [(s["vertex"], device_key)])
            for t, i in holders:
                pairs += self._back("FROM_DEVICE", t, i)
        return self._record("mapped.txns_by_device", {"device": device_key}, self._rows_from(pairs, since, until))

    def txns_by_recipient(self, r_email: str, since: datetime, until: datetime) -> list[Txn]:
        t, _, i = r_email.partition(":")
        pairs = self._back("PAYS", t, i) + self._back("PAYS_TO_ACCOUNT", t, i)
        return self._record("mapped.txns_by_recipient", {"recipient": r_email}, self._rows_from(pairs, since, until))

    def txns_in_band(self, lo: float, hi: float, since: datetime, until: datetime) -> list[Txn]:
        pairs = []
        for s in self._txn_types:
            for i, a in self.g.scan(s["vertex"]):
                amt = self._attr("Transaction.amount", s["vertex"], a)
                if amt is not None and lo <= float(amt) < hi:
                    pairs.append((s["vertex"], i))
        return self._record("mapped.txns_in_band", {"lo": lo, "hi": hi}, self._rows_from(pairs, since, until))

    def device_card_count(self, device_key: str, until: datetime) -> int:
        return len({r.card_id for r in self.txns_by_device(device_key, datetime.min, until)})

    #: Transaction and account ids are the bank's own strings (``FT26000104``), not GRAPH_GOA's numbers.
    string_ids = True

    def prior_cases(self, card_id: str, before: datetime) -> list[dict]:
        """The bank's own cases on this account (INVESTIGATED_IN), where it has any. A bank's case status is not
        a confirmed-fraud outcome, so these are listed as precedent and never counted as confirmed fraud."""
        out = []
        for s in self._account_types:
            if self.g.vertex(s["vertex"], card_id) is None:
                continue
            for t, i in self._follow("INVESTIGATED_IN", s["vertex"], card_id):
                status = self._attr("InvestigationCase.status", t, self.g.vertex(t, i))
                out.append({"case_id": f"{t}:{i}", "outcome": f"bank case, status {status or 'unknown'}"})
        return out
