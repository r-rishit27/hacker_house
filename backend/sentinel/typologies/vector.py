"""GraphRAG precedent: vector search over ClosedCase.emb, fused with graph links.

Two rankings of the bank's closed cases, kept apart and then fused:

* **Structural** — closed cases the graph *connects* to this one: the same card,
  a device profile it used, a recipient it paid (``linked_closed_cases``).
* **Vector** — closed cases whose analyst notes *read like* this case: cosine
  similarity between an embedding of the case's own description (what was
  flagged, what is unexplained, what the detectors found) and the 5,585
  ``ClosedCase.emb`` vectors stored on the vertices in GRAPH_GOA.

A cosine and a hop have no common scale, so they are fused by reciprocal rank
(RRF, k = 60). Only cases closed before the investigation's as-of time are
eligible — precedent can never come from the future.

The vectors are loaded from the graph once per process (a 5,585 x 256 matrix,
5.7 MB); one query is a single dot product.
"""

from __future__ import annotations

import os
from datetime import datetime

import numpy as np

RRF_K = 60


class VectorPrecedent:
    def __init__(self, conn) -> None:
        self.conn = conn
        self._ids: list[str] = []
        self._meta: list[dict] = []
        self._m: np.ndarray | None = None
        self._cache: dict[str, np.ndarray] = {}
        self.model = os.getenv("OPENAI_EMBEDDING_MODEL", "")
        #: Embedding tokens spent by the last search, so the answer can report them.
        self.last_tokens = 0
        self.dims = int(os.getenv("OPENAI_EMBEDDING_DIMENSIONS", "256"))

    def _load(self) -> None:
        if self._m is not None:
            return
        vecs = []
        for lo, hi in [(f"CC-{i:04d}", f"CC-{i + 500:04d}") for i in range(0, 6000, 500)]:
            res = self.conn.runInstalledQuery("closed_case_range", params={"from_id": lo, "to_id": hi, "with_emb": True},
                                              timeout=300_000)
            for b in res:
                for v in b.get("cases", []):
                    a = {k.split(".", 1)[-1]: x for k, x in v["attributes"].items()}
                    if len(a.get("emb") or []) == self.dims:
                        self._ids.append(a["case_id"])
                        self._meta.append({k: a[k] for k in ("case_id", "card_id", "outcome", "pattern", "closed_at",
                                                             "exposure_usd", "analyst_notes")})
                        vecs.append(a["emb"])
        m = np.asarray(vecs, dtype=np.float32)
        self._m = m / np.linalg.norm(m, axis=1, keepdims=True).clip(1e-9)
        self._closed = np.array([datetime.fromisoformat(x["closed_at"]) for x in self._meta])

    def _embed(self, text: str) -> np.ndarray:
        if text not in self._cache:
            from openai import OpenAI

            r = OpenAI().embeddings.create(model=self.model, dimensions=self.dims, input=[text[:4000]])
            self.last_tokens = int(getattr(r.usage, "total_tokens", 0) or 0)
            v = np.asarray(r.data[0].embedding, dtype=np.float32)
            self._cache[text] = v / max(np.linalg.norm(v), 1e-9)
        return self._cache[text]

    def search(self, text: str, before: datetime, k: int = 8) -> list[dict]:
        self.last_tokens = 0  # a cached query embedding costs nothing
        self._load()
        if self._m is None or not len(self._m):
            return []
        sims = self._m @ self._embed(text)
        sims = np.where(self._closed < np.datetime64(before), sims, -np.inf)
        top = np.argsort(-sims)[:k]
        return [self._meta[i] | {"similarity": round(float(sims[i]), 4), "analyst_notes": self._meta[i]["analyst_notes"][:280]}
                for i in top if np.isfinite(sims[i])]


def describe(f, unexplained: list[str], explanations: list[str], hits: list) -> str:
    """The case in the words an analyst's note would use — the vector query."""
    parts = [f"{'Online' if f.online else 'Card-present'} purchase of ${f.amt:,.2f}, product {f.product}"
             + (f", billing region {f.addr1}" if f.addr1 else "")
             + (f", device {f.device_new or 'seen'}" if f.device_key else "")
             + (f", {f.proxy}" if f.proxy else "") + "."]
    parts += [f"Unusual: {u}." for u in unexplained]
    parts += [f"Explained by history: {e}." for e in explanations]
    parts += [h.description for h in hits[:2]]
    return " ".join(parts)


def fuse(structural: list[dict], vector: list[dict], k: int = 8) -> list[dict]:
    """Reciprocal-rank fusion of the two rankings, each hit labelled with how it was found."""
    score: dict[str, float] = {}
    rows: dict[str, dict] = {}
    for rank, r in enumerate(structural):
        score[r["case_id"]] = score.get(r["case_id"], 0) + 1 / (RRF_K + rank + 1)
        rows.setdefault(r["case_id"], dict(r))["found_by"] = ["graph:" + r.get("linked_via", "link")]
    for rank, r in enumerate(vector):
        score[r["case_id"]] = score.get(r["case_id"], 0) + 1 / (RRF_K + rank + 1)
        row = rows.setdefault(r["case_id"], dict(r))
        row.setdefault("found_by", []).append(f"vector:{r['similarity']:.2f}")
        row.setdefault("similarity", r["similarity"])
    return [rows[c] | {"rrf": round(score[c], 5)} for c in sorted(score, key=lambda c: -score[c])][:k]
