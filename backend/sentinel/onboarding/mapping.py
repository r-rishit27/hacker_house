"""A bank's mapping onto the canonical model, and the check that it is real.

A mapping is plain JSON, reviewed by a person before it is used:

    concepts:   {"Transaction": {"status": "exact", "sources": [{"vertex": "FUNDS_TRANSFER"}, ...]}}
    relations:  {"INITIATED": {"status": "exact", "paths": [{"start": "ACCOUNT", "steps": ["reverse_FT_DEBIT_ACCT_NO"],
                                                            "end": "FUNDS_TRANSFER", "filter": null}]}}
    attributes: {"Transaction.timestamp": {"status": "approximate",
                                           "sources": [{"vertex": "FUNDS_TRANSFER", "attribute": "DATE_TIME",
                                                        "transform": "yymmddhhmm"}]}}

``validate`` refuses anything that does not exist in the bank's schema: an
unknown vertex, attribute or edge, a path whose steps do not connect, a path
that does not start and end at the vertices mapped for the relation's concepts.
A proposal from a model is only ever a proposal; this is what it must pass.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sentinel.onboarding.canonical import ATTRIBUTES, CONCEPTS, RELATIONS, TRANSFORMS
from sentinel.onboarding.schema import Schema

STATUSES = ("exact", "approximate", "unmapped")
OPS = ("=", "!=", "in", "not_in", ">", "<")


def schema_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def concept_vertices(mapping: dict, concept: str) -> set[str]:
    c = mapping.get("concepts", {}).get(concept) or {}
    return {s["vertex"] for s in c.get("sources", []) if not s.get("attribute")}


def _check_filter(schema: Schema, f: dict | None, where: str, default_vertex: str | None, problems: list[str]) -> None:
    if not f:
        return
    v = f.get("vertex") or default_vertex
    if v not in schema.vertices:
        problems.append(f"{where}: filter names unknown vertex {v!r}")
        return
    if not schema.vertices[v].attr(f.get("attribute", "")):
        problems.append(f"{where}: filter attribute {f.get('attribute')!r} is not on {v}")
    if f.get("op") not in OPS:
        problems.append(f"{where}: filter op {f.get('op')!r} is not one of {OPS}")


def walk(schema: Schema, start: str, steps: list[str]) -> set[str]:
    """The vertex types reachable from ``start`` by following ``steps`` in order."""
    here = {start}
    for edge in steps:
        here = {b for v in here for b in schema.step(v, edge)}
        if not here:
            return set()
    return here


def validate(schema: Schema, mapping: dict) -> list[str]:
    """Every reason the mapping cannot be used as it stands. Empty means it is consistent with the schema."""
    problems: list[str] = []
    concepts = mapping.get("concepts", {})
    for name, c in concepts.items():
        if name not in CONCEPTS:
            problems.append(f"concept {name!r} is not a canonical concept")
            continue
        if c.get("status") not in STATUSES:
            problems.append(f"concept {name}: status {c.get('status')!r}")
        if c.get("status") != "unmapped" and not c.get("sources"):
            problems.append(f"concept {name}: mapped but has no source")
        for s in c.get("sources", []):
            v = s.get("vertex")
            if v not in schema.vertices:
                problems.append(f"concept {name}: unknown vertex {v!r}")
                continue
            if s.get("attribute") and not schema.vertices[v].attr(s["attribute"]):
                problems.append(f"concept {name}: {v} has no attribute {s['attribute']!r}")
            _check_filter(schema, s.get("filter"), f"concept {name}", v, problems)

    for name, r in mapping.get("relations", {}).items():
        if name not in RELATIONS:
            problems.append(f"relation {name!r} is not a canonical relation")
            continue
        if r.get("status") not in STATUSES:
            problems.append(f"relation {name}: status {r.get('status')!r}")
        if r.get("status") == "unmapped":
            continue
        a, b, _ = RELATIONS[name]
        starts, ends = concept_vertices(mapping, a), concept_vertices(mapping, b)
        if not r.get("paths"):
            problems.append(f"relation {name}: mapped but has no path")
        for p in r.get("paths", []):
            where = f"relation {name} path {p.get('start')}→{p.get('end')}"
            if p.get("start") not in starts:
                problems.append(f"{where}: start {p.get('start')!r} is not a vertex mapped to {a} ({sorted(starts)})")
            if p.get("end") not in ends:
                problems.append(f"{where}: end {p.get('end')!r} is not a vertex mapped to {b} ({sorted(ends)})")
            if p.get("start") in schema.vertices:
                reached = walk(schema, p["start"], p.get("steps", []))
                bad = [e for e in p.get("steps", []) if e not in schema.edges and e not in
                       {x.reverse for x in schema.edges.values() if x.reverse}]
                if bad:
                    problems.append(f"{where}: unknown edge(s) {bad}")
                elif p.get("end") not in reached:
                    problems.append(f"{where}: steps {p.get('steps')} reach {sorted(reached) or 'nothing'}, not {p.get('end')}")
            _check_filter(schema, p.get("filter"), where, p.get("end"), problems)

    for name, at in mapping.get("attributes", {}).items():
        if name not in ATTRIBUTES:
            problems.append(f"attribute {name!r} is not a canonical attribute")
            continue
        if at.get("status") not in STATUSES:
            problems.append(f"attribute {name}: status {at.get('status')!r}")
        for s in at.get("sources", []):
            v = s.get("vertex")
            if v not in schema.vertices:
                problems.append(f"attribute {name}: unknown vertex {v!r}")
                continue
            if not schema.vertices[v].attr(s.get("attribute", "")):
                problems.append(f"attribute {name}: {v} has no attribute {s.get('attribute')!r}")
            if (s.get("transform") or "none") not in TRANSFORMS:
                problems.append(f"attribute {name}: transform {s.get('transform')!r} is not one of {TRANSFORMS}")
            _check_filter(schema, s.get("filter"), f"attribute {name}", v, problems)
    return problems


def save(mapping: dict, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(mapping, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
