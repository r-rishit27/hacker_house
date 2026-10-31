"""Score a proposed mapping against a test bank's expected mapping.

The three test schemas carry their expected mapping in a comment block
("Expected mapping (ground truth)"). The proposer never sees it; this module
reads it to measure the proposer, item by item:

* mapped or not: does the proposal agree that the bank has the thing at all;
* status: exact / approximate / unmapped, as written;
* source: for an item the bank has, does the proposal use at least one of the
  identifiers the expected line names, and none it explicitly rules out
  ("not STMT_ENTRY", "not IssuedDeviceAllocation").

A wrong mapping (the bank lacks the thing but the proposal maps it, or maps it
to a ruled-out source) is counted separately: that is the kind of error that
would feed an investigation wrong data, so it matters more than a miss.
"""

from __future__ import annotations

import re

from sentinel.onboarding.schema import Schema

_LINE = re.compile(r"^#\s{2,}(\S.*?)\s{2,}(exact|approximate|unmapped)\b\s*(.*)$")
_CONT = re.compile(r"^#\s{20,}(\S.*)$")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def expected(header: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    on, last = False, None
    for raw in header.splitlines():
        if "Expected mapping" in raw:
            on = True
            continue
        if not on:
            continue
        m = _LINE.match(raw)
        if m:
            names, status, desc = m.groups()
            keys = [n.strip() for n in names.split(",")]
            for k in keys:
                out[k] = {"status": status, "desc": desc.strip()}
            last = keys
            continue
        c = _CONT.match(raw)
        if c and last:
            for k in last:
                out[k]["desc"] += " " + c.group(1).strip()
    return out


def _schema_idents(schema: Schema) -> set[str]:
    ids = set(schema.vertices) | set(schema.edges) | {e.reverse for e in schema.edges.values() if e.reverse}
    for v in schema.vertices.values():
        ids |= {v.primary_id.name, *(a.name for a in v.attrs)}
    return ids


def _split(desc: str, known: set[str]) -> tuple[set[str], set[str]]:
    """Identifiers the expected line names, and those it rules out with "not ..."."""
    neg: set[str] = set()
    for m in re.finditer(r"\bnot\s+([^),;]+)", desc):
        neg |= {t for t in _IDENT.findall(m.group(1)) if t in known}
    pos = {t for t in _IDENT.findall(desc) if t in known} - neg
    return pos, neg


def proposed_idents(mapping: dict, kind: str, key: str) -> set[str]:
    if kind == "concept":
        item = mapping["concepts"].get(key, {})
        return {x for s in item.get("sources", []) for x in (s.get("vertex"), s.get("attribute")) if x}
    if kind == "relation":
        item = mapping["relations"].get(key, {})
        return {x for p in item.get("paths", []) for x in [*p.get("steps", []), p.get("start"), p.get("end")] if x}
    item = mapping["attributes"].get(key, {})
    return {x for s in item.get("sources", []) for x in (s.get("vertex"), s.get("attribute")) if x}


def score(schema: Schema, mapping: dict) -> dict:
    from sentinel.onboarding.canonical import ATTRIBUTES, CONCEPTS, RELATIONS

    known = _schema_idents(schema)
    rows = []
    for key, exp in expected(schema.header).items():
        kind = "concept" if key in CONCEPTS else "relation" if key in RELATIONS else "attribute" if key in ATTRIBUTES else None
        if kind is None:
            continue
        got_item = (mapping.get(kind + "s") or {}).get(key) or {"status": "unmapped"}
        got = got_item.get("status", "unmapped")
        ids = proposed_idents(mapping, kind, key)
        pos, neg = _split(exp["desc"], known)
        exp_mapped, got_mapped = exp["status"] != "unmapped", got != "unmapped"
        source_ok = None
        if exp_mapped and got_mapped:
            source_ok = bool(ids & pos) and not (ids & neg) if pos else not (ids & neg)
        wrong = (got_mapped and not exp_mapped) or bool(ids & neg) or source_ok is False
        rows.append({"key": key, "kind": kind, "expected": exp["status"], "proposed": got,
                     "mapped_agrees": exp_mapped == got_mapped, "status_agrees": exp["status"] == got,
                     "source_ok": source_ok, "wrong": wrong, "expected_names": sorted(pos), "ruled_out": sorted(neg),
                     "proposed_names": sorted(ids)})
    n = len(rows)
    both = [r for r in rows if r["source_ok"] is not None]
    return {
        "items": n,
        "mapped_agrees": sum(r["mapped_agrees"] for r in rows),
        "status_agrees": sum(r["status_agrees"] for r in rows),
        "source_ok": sum(bool(r["source_ok"]) for r in both), "source_checked": len(both),
        "wrong": sum(r["wrong"] for r in rows),
        "missed": sum(r["expected"] != "unmapped" and r["proposed"] == "unmapped" for r in rows),
        "rows": rows,
    }
