"""The embedding boundary: one model, one width, one place that batches.

Two callers need vectors and they must agree on both the model and the width,
because a 256-dimension query vector cosine-compared against a 1,536-dimension
stored vector does not fail — it silently returns nonsense. So the width lives
here, comes from ``Settings``, and is written to the graph as a ``MetaDoc`` row
by the ingest, where a later run can read it back and refuse to proceed if it
disagrees.

``fingerprint`` is what makes an interrupted ingest resumable: a row whose
stored hash matches the text it would be built from is skipped rather than
re-embedded. 5,565 closed cases is about $0.01, but it is also several minutes,
and a re-run that costs nothing is a re-run that gets used.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from openai import APIError, APIStatusError, AsyncOpenAI, RateLimitError

from sentinel.config.settings import Settings
from sentinel.domain.errors import LlmUnavailable
from sentinel.llm.client import CostMeter

logger = logging.getLogger(__name__)

#: The OpenAI embeddings endpoint accepts many inputs per call. 128 keeps one
#: request under the token ceiling for the longest closed-case note (437 chars)
#: with room to spare, and keeps a failed batch cheap to retry.
DEFAULT_BATCH_SIZE = 128

#: The API rejects an empty string. A blank note is a real row in the corpus, so
#: it is substituted rather than dropped — dropping it would misalign the result
#: list with the input list, which is the one bug in this file that would be
#: invisible.
_EMPTY_PLACEHOLDER = "(no text)"

#: The MetaDoc key the ingest writes and a later run asserts against.
DIMENSIONS_META_KEY = "embedding_dimensions"

#: The MetaDoc key naming the model, for the same reason.
MODEL_META_KEY = "embedding_model"


@dataclass
class EmbeddingService:
    """Text to vectors, batched, retried and metered.

    Responsibility: call the embeddings endpoint and nothing else. It does not
    know what a closed case is, does not write to the graph and holds no index.
    Collaborators: ``Settings`` for the model and width; ``CostMeter`` for the
    running total, shared with ``LlmClient`` when one investigation owns both;
    ``CorpusBuilder`` and ``GraphRagRetriever`` as callers.
    """

    settings: Settings
    meter: CostMeter = field(default_factory=CostMeter)
    client: AsyncOpenAI | None = None
    batch_size: int = DEFAULT_BATCH_SIZE
    max_retries: int = 3

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = AsyncOpenAI(
                api_key=self.settings.openai_api_key.get_secret_value(),
                timeout=self.settings.openai_timeout_s,
                max_retries=0,  # retried here, so every failure is logged
            )

    @property
    def model(self) -> str:
        return self.settings.openai_embedding_model

    @property
    def dimensions(self) -> int:
        return self.settings.openai_embedding_dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per input, in input order. Empty input, empty output."""
        if not texts:
            return []
        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = [
                t if t.strip() else _EMPTY_PLACEHOLDER
                for t in texts[start : start + self.batch_size]
            ]
            out.extend(await self._embed_batch(batch))
        if len(out) != len(texts):  # pragma: no cover - the API contract guarantees this
            raise LlmUnavailable(f"embeddings: asked for {len(texts)} vectors, got {len(out)}")
        return out

    async def embed_one(self, text: str) -> list[float]:
        """One vector. Convenience over :meth:`embed`, same guarantees."""
        vectors = await self.embed([text])
        return vectors[0]

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        assert self.client is not None
        last: Exception | None = None
        for attempt in range(1 + self.max_retries):
            try:
                response = await self.client.embeddings.create(
                    model=self.model, input=batch, dimensions=self.dimensions
                )
            except RateLimitError as exc:
                last = exc
                await self._backoff(attempt, "rate limited")
                continue
            except (APIStatusError, APIError) as exc:
                last = exc
                if attempt >= self.max_retries:
                    break
                await self._backoff(attempt, type(exc).__name__)
                continue
            self.meter.charge(self.model, response.usage.total_tokens if response.usage else 0, 0)
            # `data` is documented as index-ordered, but the order is what keeps
            # a vector attached to the right vertex, so it is sorted, not trusted.
            return [item.embedding for item in sorted(response.data, key=lambda d: d.index)]
        raise LlmUnavailable(f"embeddings: exhausted retries: {last}")

    async def _backoff(self, attempt: int, reason: str) -> None:
        delay = 2.0 * (2**attempt)
        logger.warning("embeddings: %s, retrying in %.0fs", reason, delay)
        await asyncio.sleep(delay)

    @staticmethod
    def fingerprint(text: str) -> str:
        """A short stable hash of the text a stored vector was built from."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity, 0.0 when either side has no magnitude.

    Used by the tests and by any caller holding two lone vectors.
    ``VectorIndex`` does not call this — it normalises once and takes a dot
    product over the whole matrix, which is the same number far faster.
    """
    if len(a) != len(b):
        raise ValueError(f"cosine over {len(a)} and {len(b)} dimensions")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
