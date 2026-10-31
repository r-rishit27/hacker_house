"""A bank's graph schema, read from its GSQL schema-change job.

Onboarding starts from what the bank already has: the ``ADD VERTEX`` and
``ADD EDGE`` statements that define its graph. Nothing about the bank's
vocabulary is assumed here; this only records names, types and directions so
the mapper and the validator can check every name against the real schema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Attr:
    name: str
    type: str  # STRING, INT, DOUBLE, BOOL, DATETIME, ...


@dataclass(frozen=True)
class VertexType:
    name: str
    primary_id: Attr
    attrs: tuple[Attr, ...]

    def attr(self, name: str) -> Attr | None:
        if name == self.primary_id.name:
            return self.primary_id
        return next((a for a in self.attrs if a.name == name), None)


@dataclass(frozen=True)
class EdgeType:
    name: str
    directed: bool
    pairs: tuple[tuple[str, str], ...]  # (from vertex type, to vertex type), one per "FROM x, TO y"
    attrs: tuple[Attr, ...]
    reverse: str | None  # the reverse edge's name, for directed edges that declare one


@dataclass
class Schema:
    graph: str
    vertices: dict[str, VertexType] = field(default_factory=dict)
    edges: dict[str, EdgeType] = field(default_factory=dict)
    header: str = ""  # the comment block above the schema, kept verbatim

    def steps_from(self, vertex: str) -> list[tuple[str, str]]:
        """Every (edge name, next vertex type) you can take from ``vertex``, reverse edges included."""
        out = []
        for e in self.edges.values():
            for a, b in e.pairs:
                if a == vertex:
                    out.append((e.name, b))
                if b == vertex and (not e.directed or e.reverse):
                    out.append((e.name if not e.directed else e.reverse, a))
        return out

    def step(self, vertex: str, edge: str) -> list[str]:
        """The vertex types reached from ``vertex`` along ``edge`` (a forward or a reverse_ name)."""
        return [b for name, b in self.steps_from(vertex) if name == edge]

    def describe(self) -> dict:
        """A compact, JSON-able view for the mapper."""
        return {
            "graph": self.graph,
            "vertices": {v.name: {"primary_id": f"{v.primary_id.name} {v.primary_id.type}",
                                  "attributes": {a.name: a.type for a in v.attrs}} for v in self.vertices.values()},
            "edges": {e.name: {"directed": e.directed, "pairs": [f"{a} -> {b}" for a, b in e.pairs],
                               "reverse": e.reverse, "attributes": {a.name: a.type for a in e.attrs}}
                      for e in self.edges.values()},
        }


_ATTR = re.compile(r"(\w+)\s+(STRING|INT|UINT|DOUBLE|FLOAT|BOOL|DATETIME|LIST<\w+>|SET<\w+>)", re.I)
_VERTEX = re.compile(r"ADD\s+VERTEX\s+(\w+)\s*\((.*?)\)\s*(?:WITH[^;]*)?;", re.I | re.S)
_EDGE = re.compile(r"ADD\s+(DIRECTED|UNDIRECTED)\s+EDGE\s+(\w+)\s*\((.*?)\)\s*(?:WITH\s+REVERSE_EDGE\s*=\s*\"(\w+)\")?\s*;",
                   re.I | re.S)
_PAIR = re.compile(r"FROM\s+(\w+)\s*,\s*TO\s+(\w+)", re.I)
_GRAPH = re.compile(r"CREATE\s+GRAPH\s+(\w+)", re.I)


def _attrs(body: str) -> list[Attr]:
    return [Attr(n, t.upper()) for n, t in _ATTR.findall(body)]


def parse(text: str) -> Schema:
    g = _GRAPH.search(text)
    header = "\n".join(line for line in text.splitlines() if line.startswith("#"))
    schema = Schema(graph=g.group(1) if g else "unnamed", header=header)
    for name, body in _VERTEX.findall(text):
        body = re.sub(r"PRIMARY_ID\s+", "", body, flags=re.I)
        attrs = _attrs(body)
        if not attrs:
            raise ValueError(f"vertex {name} has no readable attributes")
        schema.vertices[name] = VertexType(name, attrs[0], tuple(attrs[1:]))
    for kind, name, body, reverse in _EDGE.findall(text):
        pairs = tuple(_PAIR.findall(body))
        if not pairs:
            raise ValueError(f"edge {name} has no FROM/TO pair")
        rest = _PAIR.sub("", body)
        schema.edges[name] = EdgeType(name, kind.upper() == "DIRECTED", pairs, tuple(_attrs(rest)), reverse or None)
    for e in schema.edges.values():
        for a, b in e.pairs:
            for v in (a, b):
                if v not in schema.vertices:
                    raise ValueError(f"edge {e.name} refers to undeclared vertex {v}")
    return schema


def load(path: str | Path) -> Schema:
    return parse(Path(path).read_text(encoding="utf-8"))
