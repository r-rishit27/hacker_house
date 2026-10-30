"""The vector side of retrieval: one normalised matrix, one dot product.

TigerGraph 4.2.5 on Savanna stores the vectors — `ClosedCase.emb` and
`PolicyDoc.emb` are `LIST<DOUBLE>` on the vertices themselves, so a retrieved
hit is a graph vertex with edges, not a row in a sidecar store. What it does not
give this schema is a native ANN index, so the cosine ranking is computed here.

At the sizes involved that is not a compromise. 5,565 closed cases plus 46
policy chunks at 256 dimensions is a 5.6 MB float32 matrix; one query is a
single `(N, 256) @ (256,)` product, about 1.4 million multiply-adds, which numpy
does in well under a millisecond. An ANN index would add a dependency, an
approximation and a build step to save nothing measurable.

Vectors are L2-normalised once at load, so cosine similarity *is* the dot
product and no per-query division happens at all.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

logger = logging.getLogger(__name__)

Matrix = npt.NDArray[np.float32]


@dataclass(frozen=True, slots=True)
class IndexedDoc:
    """One embedded row, with the metadata a hit needs to explain itself."""

    id: str
    kind: str
    title: str
    text: str
    emb: Sequence[float]
    #: Everything else the ranker or the UI may want: pattern, outcome,
    #: opened_at, card_id. Kept untyped because the two kinds carry different
    #: fields and neither is worth a second class.
    meta: dict[str, object]


@dataclass(frozen=True, slots=True)
class ScoredId:
    """One ranked result: what it is, how it scored, and by which ranking."""

    id: str
    kind: str
    score: float
    rank: int
    strategy: str


class VectorIndex:
    """An in-memory cosine index over the graph's stored embeddings.

    Responsibility: hold the matrix and answer a top-k query. It performs no
    I/O, so a test warms it from a list and the retriever warms it from the
    graph through exactly the same method.
    Collaborators: ``GraphRagRetriever`` warms it and searches it;
    ``CorpusBuilder`` writes the vectors it later reads.
    """

    def __init__(self) -> None:
        self._matrix: Matrix = np.zeros((0, 0), dtype=np.float32)
        self._docs: list[IndexedDoc] = []
        self._by_id: dict[str, IndexedDoc] = {}
        self._kinds: npt.NDArray[np.str_] = np.array([], dtype="<U16")

    # ── loading ──────────────────────────────────────────────────────────────

    def warm(self, docs: Iterable[IndexedDoc]) -> int:
        """Replace the index contents. Rows with no vector are dropped.

        A row whose vector has the wrong width is dropped loudly rather than
        padded: mixing widths silently produces a ranking that looks plausible
        and is meaningless, which is the worst possible failure here.
        """
        usable = [doc for doc in docs if doc.emb]
        if not usable:
            self._matrix = np.zeros((0, 0), dtype=np.float32)
            self._docs, self._by_id = [], {}
            self._kinds = np.array([], dtype="<U16")
            return 0

        width = len(usable[0].emb)
        kept = [doc for doc in usable if len(doc.emb) == width]
        if len(kept) != len(usable):
            logger.warning(
                "vector index: dropped %d row(s) whose width was not %d",
                len(usable) - len(kept),
                width,
            )

        matrix = np.asarray([doc.emb for doc in kept], dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        self._matrix = matrix / norms
        self._docs = kept
        self._by_id = {doc.id: doc for doc in kept}
        self._kinds = np.array([doc.kind for doc in kept], dtype="<U16")
        logger.info("vector index warmed: %d rows at %d dimensions", len(kept), width)
        return len(kept)

    # ── querying ─────────────────────────────────────────────────────────────

    def search(
        self, vector: Sequence[float], k: int = 50, kind: str | None = None
    ) -> list[ScoredId]:
        """Top ``k`` by cosine, optionally restricted to one kind."""
        if not self.size or not vector:
            return []
        query = np.asarray(vector, dtype=np.float32)
        if query.shape[0] != self._matrix.shape[1]:
            raise ValueError(
                f"query has {query.shape[0]} dimensions; the index holds "
                f"{self._matrix.shape[1]}. Re-ingest, or fix OPENAI_EMBEDDING_DIMENSIONS"
            )
        norm = float(np.linalg.norm(query))
        if norm == 0.0:
            return []

        scores = self._matrix @ (query / norm)
        mask = self._kinds == kind if kind is not None else None
        if mask is not None:
            scores = np.where(mask, scores, -np.inf)
            available = int(mask.sum())
        else:
            available = len(self._docs)
        if available == 0:
            return []

        take = min(k, available)
        # argpartition is O(n) where a full sort is O(n log n); only the top
        # slice is then sorted. At 5,565 rows this is noise, but the retriever
        # runs it twice per case and the habit costs nothing.
        top = np.argpartition(-scores, take - 1)[:take]
        top = top[np.argsort(-scores[top])]
        return [
            ScoredId(
                id=self._docs[int(i)].id,
                kind=self._docs[int(i)].kind,
                score=float(scores[int(i)]),
                rank=rank,
                strategy="vector",
            )
            for rank, i in enumerate(top, start=1)
            if np.isfinite(scores[int(i)])
        ]

    def get(self, doc_id: str) -> IndexedDoc | None:
        return self._by_id.get(doc_id)

    def ids_of_kind(self, kind: str) -> list[str]:
        return [doc.id for doc in self._docs if doc.kind == kind]

    @property
    def size(self) -> int:
        return len(self._docs)

    @property
    def dimensions(self) -> int:
        return int(self._matrix.shape[1]) if self.size else 0

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for doc in self._docs:
            out[doc.kind] = out.get(doc.kind, 0) + 1
        return out

    # ── disk cache ───────────────────────────────────────────────────────────

    def save(self, path: Path) -> None:
        """Cache the index so a second run does not re-read 5,565 vectors.

        The graph remains the source of truth; this is only a read-through
        cache, and :meth:`load` refuses it whenever the row count or the width
        has moved.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            matrix=self._matrix,
            ids=np.array([doc.id for doc in self._docs], dtype=object),
            kinds=self._kinds,
            titles=np.array([doc.title for doc in self._docs], dtype=object),
            texts=np.array([doc.text for doc in self._docs], dtype=object),
            metas=np.array([doc.meta for doc in self._docs], dtype=object),
        )
        logger.info("vector index cached to %s (%d rows)", path, self.size)

    def load(
        self, path: Path, *, expect_rows: int | None = None, expect_dims: int | None = None
    ) -> bool:
        """Warm from the cache. Returns False when it is absent or stale."""
        if not path.exists():
            return False
        try:
            data = np.load(path, allow_pickle=True)
        except (OSError, ValueError) as exc:
            logger.warning("vector cache %s unreadable (%s); re-reading the graph", path, exc)
            return False
        matrix = np.asarray(data["matrix"], dtype=np.float32)
        rows, dims = matrix.shape if matrix.ndim == 2 else (0, 0)
        if expect_rows is not None and rows != expect_rows:
            logger.info("vector cache holds %d rows, graph has %d; re-reading", rows, expect_rows)
            return False
        if expect_dims is not None and dims != expect_dims:
            logger.info("vector cache is %dd, configured for %dd; re-reading", dims, expect_dims)
            return False

        self._matrix = matrix
        self._docs = [
            IndexedDoc(
                id=str(doc_id),
                kind=str(kind),
                title=str(title),
                text=str(text),
                emb=(),  # the matrix holds the vectors; the rows need not repeat them
                meta=dict(meta) if isinstance(meta, dict) else {},
            )
            for doc_id, kind, title, text, meta in zip(
                data["ids"],
                data["kinds"],
                data["titles"],
                data["texts"],
                data["metas"],
                strict=True,
            )
        ]
        self._by_id = {doc.id: doc for doc in self._docs}
        self._kinds = np.asarray(data["kinds"], dtype="<U16")
        logger.info("vector index loaded from cache: %d rows at %dd", rows, dims)
        return True
