"""The repository port: the only door to TigerGraph."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


class GraphRepository(ABC):
    """The only door to TigerGraph.

    Responsibility: transport, authentication, retry and normalisation. It knows
    nothing about fraud, evidence or policy, and it applies no thresholds — a
    repository that decided anything would put that decision beyond unit test.
    Collaborators: TokenManager and ResponseNormalizer in the REST implementation;
    nothing at all in the fake.

    Six abstract operations cover every TigerGraph call site in the system: the
    installed queries, the case write-back's vertex and edge upserts, the
    validator's existence checks and the health endpoint's vertex census.
    ``upsert_batch`` is defaulted rather than abstract so that a fake and any
    future backend inherit a correct — if chattier — implementation.
    """

    @abstractmethod
    async def run_query(self, name: str, params: Mapping[str, Any]) -> dict[str, Any]:
        """Run an installed query and return its flattened, sanitised result.

        The returned dict is keyed by the query's ``PRINT ... AS`` names. Attribute
        keys inside printed vertex rows keep their GSQL alias prefix (``T.txn_id``);
        stripping that is the caller's business, through ``ResponseNormalizer``.

        Raises:
            QueryFailed: the query ran and TigerGraph returned an error payload.
            GraphUnavailable: the workspace could not be reached or kept waking.
        """

    @abstractmethod
    async def upsert_vertex(self, vtype: str, vid: str, attrs: Mapping[str, Any]) -> int:
        """Upsert one vertex. Returns the number of vertices TigerGraph accepted."""

    @abstractmethod
    async def upsert_edge(
        self,
        etype: str,
        src_type: str,
        src: str,
        dst_type: str,
        dst: str,
        attrs: Mapping[str, Any] | None = None,
    ) -> int:
        """Upsert one edge. Returns the number of edges TigerGraph accepted."""

    @abstractmethod
    async def get_vertex(self, vtype: str, vid: str) -> dict[str, Any] | None:
        """One vertex as TigerGraph returns it: ``{v_id, v_type, attributes}``.

        Returns ``None`` when the vertex is absent, and raises only when the lookup
        itself failed. Callers decide whether absence is an error; ``eval/validate.py``
        collapsed the two and so reported a transient auth failure as an invalid
        submission.
        """

    @abstractmethod
    async def get_vertices(self, vtype: str, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Several vertices by id, keyed by id. Absent ids are simply missing."""

    @abstractmethod
    async def stat_vertex_counts(self) -> dict[str, int]:
        """Vertex counts per type, for the health endpoint and the load check."""

    # ── batched write, defaulted so a fake inherits it ───────────────────────

    async def upsert_batch(
        self,
        vertices: Sequence[VertexUpsert] = (),
        edges: Sequence[EdgeUpsert] = (),
    ) -> UpsertResult:
        """Upsert many vertices and edges, returning what was accepted.

        The default implementation loops over the single-item operations, which
        is what a fake wants. ``TigerGraphRestRepository`` overrides it with one
        POST: a case write-back touches up to thirty edges, and thirty round
        trips to Savanna is twenty seconds the demo does not have.
        """
        accepted_v = 0
        for vertex in vertices:
            accepted_v += await self.upsert_vertex(vertex.vtype, vertex.vid, vertex.attrs)
        accepted_e = 0
        for edge in edges:
            accepted_e += await self.upsert_edge(
                edge.etype, edge.src_type, edge.src, edge.dst_type, edge.dst, edge.attrs
            )
        return UpsertResult(vertices=accepted_v, edges=accepted_e)


@dataclass(frozen=True, slots=True)
class VertexUpsert:
    """One vertex in a batched write."""

    vtype: str
    vid: str
    attrs: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EdgeUpsert:
    """One edge in a batched write."""

    etype: str
    src_type: str
    src: str
    dst_type: str
    dst: str
    attrs: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UpsertResult:
    """What TigerGraph said it accepted."""

    vertices: int
    edges: int
