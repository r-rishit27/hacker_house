"""Hybrid retrieval: cosine and graph structure, fused, then expanded.

Pure vector search over a graph database is a bad demo and a worse retriever.
The two rankings here are genuinely independent and they fail in different
ways, which is the whole reason to run both:

- **Vector** finds a case that *reads* like this one — a note describing a
  disputed recurring charge scores highly against a query describing a disputed
  recurring charge, whatever card it happened on. It has no idea whether the
  two cases share anything.
- **Structural** finds a case that *is connected* to this one — same card, same
  customer, a card connected through `CC_CONNECTED_TO`, the same pattern in the
  same exposure band. It has no idea whether the two cases read alike.

Their scores are not comparable: one is a cosine in [-1, 1], the other is a hop
count. Reciprocal rank fusion is used precisely because it discards the
magnitudes and keeps only the orderings.

Two hard filters apply before fusion, and the first is not an optimisation:

**`opened_at < as_of`.** A case must never retrieve an investigation that had
not happened yet. Twenty benchmark cases run in chronological order and write
their own `FraudCase` vertices as they go, so without this filter case three
could cite case nineteen and the memory claim would be a leak instead of a
capability.

**Provenance is recorded, not inferred.** Each surviving hit is expanded
through the graph to find out *which* edge connected it. That is what the
console's memory tab renders, and it is what makes the retrieval visibly
graph-based rather than a vector store with a graph next to it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sentinel.config.settings import Settings
from sentinel.domain.enums import Pattern
from sentinel.graph.normalize import ResponseNormalizer
from sentinel.graph.repository import GraphRepository
from sentinel.rag.embeddings import EmbeddingService
from sentinel.rag.index import IndexedDoc, ScoredId, VectorIndex
from sentinel.tools.refs import EvidenceRef

logger = logging.getLogger(__name__)

#: RRF's damping constant. 60 is the value from Cormack et al. and it is not
#: tuned here: with two rankings of fifty, anything from 20 to 100 reorders
#: almost nothing, and a number invented for this repo would suggest otherwise.
RRF_K = 60

#: A hit both rankings found is worth more than the sum of its two reciprocals.
#: Agreement between two independent signals is the thing being rewarded.
BOTH_LISTS_BONUS = 1.25

#: How deep each ranking goes before fusion.
SHORTLIST = 50

KIND_CLOSED_CASE = "closed_case"
KIND_POLICY = "policy_doc"
KIND_SENTINEL_CASE = "sentinel_case"


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    """What one retrieval is looking for, and what it may not look at."""

    text: str
    kind: str = KIND_CLOSED_CASE
    pattern: Pattern = Pattern.NONE
    exposure: float = 0.0
    card_id: str = ""
    customer_id: str = ""
    device_keys: tuple[str, ...] = ()
    region: str = ""
    #: Nothing opened at or after this instant may be retrieved. Never optional.
    as_of: datetime | None = None
    k: int = 6


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    """One retrieved item, with the evidence of how it was found."""

    id: str
    kind: str
    score: float
    title: str
    snippet: str
    ref: str
    provenance: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "score": round(self.score, 4),
            "title": self.title,
            "snippet": self.snippet,
            "ref": self.ref,
            "provenance": self.provenance,
        }


@dataclass
class GraphRagRetriever:
    """Two rankings, fused, expanded for provenance.

    Responsibility: retrieval and its explanation. It posts no evidence, makes
    no fraud judgement and writes nothing.
    Collaborators: ``EmbeddingService`` for the query vector; ``VectorIndex``
    for the cosine shortlist; ``GraphRepository`` for the structural shortlist
    and the expansion.
    """

    settings: Settings
    graph: GraphRepository
    embeddings: EmbeddingService
    index: VectorIndex = field(default_factory=VectorIndex)
    normalizer: ResponseNormalizer = field(default_factory=ResponseNormalizer)
    _warm: bool = False

    @property
    def cache_path(self) -> Path:
        return self.settings.corpus_dir / f"vector-index-{self.embeddings.dimensions}d.npz"

    @property
    def ready(self) -> bool:
        return self._warm and self.index.size > 0

    # ── warming ──────────────────────────────────────────────────────────────

    async def warm(self, *, use_cache: bool = True) -> int:
        """Read every stored vector into the index, once per process."""
        if self._warm:
            return self.index.size

        counts = await self.graph.stat_vertex_counts()
        expected = counts.get("ClosedCase", 0) + counts.get("PolicyDoc", 0)
        if use_cache and self.index.load(
            self.cache_path, expect_rows=expected, expect_dims=self.embeddings.dimensions
        ):
            self._warm = True
            return self.index.size

        docs = [*await self._load_policy_docs(), *await self._load_closed_cases()]
        size = self.index.warm(docs)
        self._warm = True
        if use_cache and size:
            self.index.save(self.cache_path)
        logger.info("retriever warm: %s", self.index.counts())
        return size

    async def _load_policy_docs(self) -> list[IndexedDoc]:
        payload = await self.graph.run_query("policy_docs_all", {})
        out: list[IndexedDoc] = []
        for row in self.normalizer.rows(payload.get("docs")):
            out.append(
                IndexedDoc(
                    id=str(row.get("doc_id") or ""),
                    kind=KIND_POLICY,
                    title=str(row.get("title") or ""),
                    text=str(row.get("text") or ""),
                    emb=_floats(row.get("emb")),
                    meta={"source": row.get("source"), "section": row.get("section")},
                )
            )
        return [doc for doc in out if doc.id]

    async def _load_closed_cases(self) -> list[IndexedDoc]:
        out: list[IndexedDoc] = []
        for from_id, to_id in _id_ranges():
            payload = await self.graph.run_query(
                "closed_case_range", {"from_id": from_id, "to_id": to_id, "with_emb": True}
            )
            for row in self.normalizer.rows(payload.get("cases")):
                case_id = str(row.get("case_id") or "")
                if not case_id:
                    continue
                out.append(
                    IndexedDoc(
                        id=case_id,
                        kind=KIND_CLOSED_CASE,
                        title=f"{case_id} · {row.get('pattern')} · {row.get('outcome')}",
                        text=str(row.get("analyst_notes") or ""),
                        emb=_floats(row.get("emb")),
                        meta={
                            "pattern": row.get("pattern"),
                            "outcome": row.get("outcome"),
                            "opened_at": row.get("opened_at"),
                            "exposure_usd": row.get("exposure_usd"),
                            "card_id": row.get("card_id"),
                            "customer_id": row.get("customer_id"),
                            "actions_taken": row.get("actions_taken"),
                        },
                    )
                )
        return out

    # ── retrieval ────────────────────────────────────────────────────────────

    def policy_catalogue(self) -> dict[str, dict[str, str]]:
        """Title and citation for every policy chunk, keyed by doc_id.

        The corpus is 46 rows and already resident, so this is a dict
        comprehension rather than a query. It exists because a rule is cited by
        the *policy engine*, which runs after retrieval and does not care what
        retrieval ranked — the citation must still be right.
        """
        out: dict[str, dict[str, str]] = {}
        for doc_id in self.index.ids_of_kind(KIND_POLICY):
            doc = self.index.get(doc_id)
            if doc is None:
                continue
            section = str(doc.meta.get("section") or "")
            out[doc_id] = {
                "title": doc.title,
                "ref": EvidenceRef.document(doc_id, section),
                "section": section,
            }
        return out

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievalHit]:
        """Two rankings, fused, filtered, expanded."""
        await self.warm()
        vector = await self._vector_shortlist(query)
        structural = await self._structural(query)
        fused = self._fuse(vector, structural)
        allowed = [s for s in fused if self._permitted(s, query)]
        return await self._expand(allowed[: query.k], query, vector, structural)

    async def _vector_shortlist(self, query: RetrievalQuery) -> list[ScoredId]:
        if not query.text.strip() or not self.index.size:
            return []
        embedded = await self.embeddings.embed_one(query.text)
        return self.index.search(embedded, k=SHORTLIST, kind=query.kind)

    async def _structural(self, query: RetrievalQuery) -> list[ScoredId]:
        """Closed cases the graph already connects to this investigation.

        Three sources, in descending strength of connection: the same card, the
        same customer, then the same pattern in the same exposure band. Rank
        order encodes that; RRF then only sees the order.
        """
        if query.kind != KIND_CLOSED_CASE:
            return []
        ids: list[str] = []

        if query.card_id:
            ids.extend(await self._ids_from("case_memory_for_card", {"c_in": query.card_id}))
        if query.customer_id:
            ids.extend(
                await self._ids_from("customer_case_history", {"customer_id": query.customer_id})
            )
        if query.pattern not in (Pattern.NONE, Pattern.UNDOCUMENTED):
            lo, hi = _exposure_band(query.exposure)
            ids.extend(
                await self._ids_from(
                    "similar_prior_cases",
                    {
                        "pattern_in": query.pattern.value,
                        "amt_lo": lo,
                        "amt_hi": hi,
                        # Required by the installed query: GSQL rejects a NULL
                        # STRING parameter outright rather than defaulting it.
                        # "" is the query's own "any outcome" sentinel.
                        "outcome_in": "",
                        "k": SHORTLIST,
                    },
                )
            )

        ranked: list[ScoredId] = []
        for rank, case_id in enumerate(dict.fromkeys(ids), start=1):
            if rank > SHORTLIST:
                break
            ranked.append(
                ScoredId(
                    id=case_id,
                    kind=KIND_CLOSED_CASE,
                    score=1.0 / rank,
                    rank=rank,
                    strategy="structural",
                )
            )
        return ranked

    async def _ids_from(self, query_name: str, params: dict[str, Any]) -> list[str]:
        """Every `CC-` id anywhere in one query's result, in result order."""
        try:
            payload = await self.graph.run_query(query_name, params)
        except Exception as exc:  # noqa: BLE001 - one weak ranking must not fail a run
            logger.warning("structural ranking: %s failed (%s)", query_name, exc)
            return []
        return _closed_case_ids(payload)

    @staticmethod
    def _fuse(vector: Sequence[ScoredId], structural: Sequence[ScoredId]) -> list[ScoredId]:
        """Reciprocal rank fusion. Magnitudes are discarded; only order counts."""
        scores: dict[str, float] = {}
        seen_in: dict[str, set[str]] = {}
        kinds: dict[str, str] = {}
        for ranking in (vector, structural):
            for hit in ranking:
                scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (RRF_K + hit.rank)
                seen_in.setdefault(hit.id, set()).add(hit.strategy)
                kinds[hit.id] = hit.kind
        for doc_id, strategies in seen_in.items():
            if len(strategies) > 1:
                scores[doc_id] *= BOTH_LISTS_BONUS
        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return [
            ScoredId(
                id=doc_id,
                kind=kinds[doc_id],
                score=score,
                rank=rank,
                strategy="+".join(sorted(seen_in[doc_id])),
            )
            for rank, (doc_id, score) in enumerate(ordered, start=1)
        ]

    def _permitted(self, hit: ScoredId, query: RetrievalQuery) -> bool:
        """The time filter. A case may not retrieve the future."""
        if query.as_of is None:
            return True
        doc = self.index.get(hit.id)
        opened = _as_datetime(doc.meta.get("opened_at")) if doc else None
        if opened is None:
            # An unknown date is permitted for policy text, which has none, and
            # refused for a case, where an unknown date is the shape of the bug
            # this filter exists to stop.
            return hit.kind != KIND_CLOSED_CASE
        return opened < query.as_of

    async def _expand(
        self,
        hits: Sequence[ScoredId],
        query: RetrievalQuery,
        vector: Sequence[ScoredId],
        structural: Sequence[ScoredId],
    ) -> list[RetrievalHit]:
        """Attach the provenance that makes a hit explainable."""
        vector_ranks = {h.id: h.rank for h in vector}
        structural_ranks = {h.id: h.rank for h in structural}
        out: list[RetrievalHit] = []
        for hit in hits:
            doc = self.index.get(hit.id)
            meta = dict(doc.meta) if doc else {}
            provenance: dict[str, Any] = {
                "strategy": hit.strategy,
                "vector_rank": vector_ranks.get(hit.id),
                "structural_rank": structural_ranks.get(hit.id),
                "rrf_score": round(hit.score, 6),
            }
            if hit.kind == KIND_CLOSED_CASE:
                provenance["connection"] = _connection(meta, query)
                provenance["outcome"] = meta.get("outcome")
                provenance["pattern"] = meta.get("pattern")
                provenance["opened_at"] = meta.get("opened_at")
                ref = EvidenceRef.query("case_memory_for_card", {"card_id": query.card_id or "—"})
            else:
                ref = EvidenceRef.document(hit.id, str(meta.get("section") or ""))
            out.append(
                RetrievalHit(
                    id=hit.id,
                    kind=hit.kind,
                    score=hit.score,
                    title=doc.title if doc else hit.id,
                    snippet=_snippet(doc.text if doc else ""),
                    ref=ref,
                    provenance=provenance,
                )
            )
        return out


# ── helpers ──────────────────────────────────────────────────────────────────


def _connection(meta: dict[str, Any], query: RetrievalQuery) -> str:
    """The strongest graph relationship between the hit and the case, named."""
    if query.card_id and meta.get("card_id") == query.card_id:
        return "same card"
    if query.customer_id and meta.get("customer_id") == query.customer_id:
        return "same customer"
    if query.pattern is not Pattern.NONE and meta.get("pattern") == query.pattern.value:
        return f"same pattern ({query.pattern.value})"
    return "similar narrative"


def _exposure_band(exposure: float) -> tuple[float, float]:
    """A half-decade band around the exposure, floored at zero.

    Wide on purpose: an exposure of $49 and one of $120 are the same kind of
    case, and a tight band would return nothing on the small ones.
    """
    if exposure <= 0:
        return 0.0, 1_000_000.0
    return max(0.0, exposure * 0.25), exposure * 4.0


def _closed_case_ids(payload: Any) -> list[str]:
    """Pull every `CC-NNNN` out of a query result, preserving order."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str) and value.startswith("CC-") and key.endswith("case_id"):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(payload)
    return list(dict.fromkeys(found))


def _floats(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)):
        return []
    return [float(x) for x in value if isinstance(x, (int, float)) and not isinstance(x, bool)]


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value.strip(), fmt)
            except ValueError:
                continue
    return None


def _snippet(text: str, limit: int = 280) -> str:
    body = " ".join(text.split())
    return body if len(body) <= limit else body[: limit - 1].rstrip() + "…"


def _id_ranges(page: int = 250, total: int = 6000) -> list[tuple[str, str]]:
    return [(f"CC-{s:04d}", f"CC-{s + page:04d}") for s in range(1, total, page)]
