"""An in-memory GraphRepository, so the system is testable without a network.

Why this exists at all: the Savanna workspace auto-stops and takes about 45
seconds to wake, and the test suite must run on a laptop with no credentials.
Every unit and integration test in this repository runs against this class; only
``tests/live/`` touches the real workspace.

It is deliberately dumb. It records what it was asked and returns what it was
seeded with. It does not simulate GSQL, because a fake that reimplements the
thing it is faking tests the fake.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sentinel.domain.errors import QueryFailed, VertexNotFound
from sentinel.graph.repository import GraphRepository


@dataclass(frozen=True, slots=True)
class RecordedCall:
    """One thing the fake was asked to do, for assertions."""

    kind: str
    name: str
    params: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _Seeded:
    payload: dict[str, Any]
    params: dict[str, Any] | None


@dataclass
class FakeGraphRepository(GraphRepository):
    """A seedable stand-in for TigerGraph.

    Responsibility: return canned query payloads and vertices, and record every
    call so a test can assert what the agent asked.
    Collaborators: none. That is the point.

    Seeding a query with ``params`` makes the match conditional: the seeded pairs
    must all be present in the call's parameters. That is how a test pins
    "``region_novelty`` was called with the transaction's ``ts``, not the alert's"
    without stubbing the whole call.
    """

    queries: dict[str, list[_Seeded]] = field(default_factory=dict)
    vertices: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    calls: list[RecordedCall] = field(default_factory=list)
    upserted_vertices: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    upserted_edges: list[tuple[str, str, str, str, str, dict[str, Any]]] = field(
        default_factory=list
    )
    #: Set to raise from the next call, to exercise the failure paths.
    fail_with: Exception | None = None

    # ── seeding ──────────────────────────────────────────────────────────────

    def seed_query(
        self,
        name: str,
        payload: Mapping[str, Any],
        params: Mapping[str, Any] | None = None,
    ) -> FakeGraphRepository:
        """Register a payload for ``name``. Later seeds are matched first."""
        seeded = _Seeded(dict(payload), dict(params) if params is not None else None)
        self.queries.setdefault(name, []).insert(0, seeded)
        return self

    def seed_vertex(self, vtype: str, vid: str, attrs: Mapping[str, Any]) -> FakeGraphRepository:
        self.vertices[(vtype, vid)] = {
            "v_id": vid,
            "v_type": vtype,
            "attributes": dict(attrs),
        }
        return self

    def seed_counts(self, counts: Mapping[str, int]) -> FakeGraphRepository:
        self.counts.update(counts)
        return self

    # ── assertions ───────────────────────────────────────────────────────────

    def called(self, name: str) -> list[RecordedCall]:
        return [c for c in self.calls if c.name == name]

    def call_count(self, name: str | None = None) -> int:
        return len(self.calls if name is None else self.called(name))

    # ── the port ─────────────────────────────────────────────────────────────

    async def run_query(self, name: str, params: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append(RecordedCall("query", name, dict(params)))
        self._maybe_fail()
        for seeded in self.queries.get(name, []):
            if seeded.params is None or all(params.get(k) == v for k, v in seeded.params.items()):
                return dict(seeded.payload)
        raise QueryFailed(
            f"fake has no payload seeded for '{name}' with {dict(params)}",
            query=name,
        )

    async def upsert_vertex(self, vtype: str, vid: str, attrs: Mapping[str, Any]) -> int:
        self.calls.append(RecordedCall("upsert_vertex", vtype, {"id": vid}))
        self._maybe_fail()
        self.upserted_vertices.append((vtype, vid, dict(attrs)))
        self.seed_vertex(vtype, vid, attrs)
        return 1

    async def upsert_edge(
        self,
        etype: str,
        src_type: str,
        src: str,
        dst_type: str,
        dst: str,
        attrs: Mapping[str, Any] | None = None,
    ) -> int:
        self.calls.append(RecordedCall("upsert_edge", etype, {"src": src, "dst": dst}))
        self._maybe_fail()
        self.upserted_edges.append((etype, src_type, src, dst_type, dst, dict(attrs or {})))
        return 1

    async def get_vertex(self, vtype: str, vid: str) -> dict[str, Any] | None:
        self.calls.append(RecordedCall("get_vertex", vtype, {"id": vid}))
        self._maybe_fail()
        return self.vertices.get((vtype, vid))

    async def get_vertices(self, vtype: str, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        self.calls.append(RecordedCall("get_vertices", vtype, {"ids": list(ids)}))
        self._maybe_fail()
        found: dict[str, dict[str, Any]] = {}
        for vid in ids:
            vertex = self.vertices.get((vtype, vid))
            if vertex is not None:
                found[vid] = vertex
        return found

    async def stat_vertex_counts(self) -> dict[str, int]:
        self.calls.append(RecordedCall("stat", "*", {}))
        self._maybe_fail()
        return dict(self.counts)

    # ── internals ────────────────────────────────────────────────────────────

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with


def missing_vertex_error(vtype: str, vid: str) -> VertexNotFound:
    """Build the error a caller raises when absence genuinely is an error."""
    return VertexNotFound(f"{vtype} '{vid}' is not in the graph", vertex_type=vtype, vertex_id=vid)
