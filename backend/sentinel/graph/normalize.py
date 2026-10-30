"""TigerGraph's wire format, turned into something the rest of Sentinel can read.

Three shapes arrive from REST++ and none of them is the shape a caller wants:

1. ``results`` is a *list of blocks*, one per ``PRINT`` statement, each usually
   holding a single key. Callers want one dict.
2. A projected ``PRINT T[T.txn_id, ...]`` prefixes every attribute with the GSQL
   alias, so ``window`` rows carry ``T.txn_id`` while ``PRINT R AS card`` — which
   prints the whole vertex — carries a bare ``card_id``. Twelve of the sixteen
   installed queries are of the first kind, four of the second.
3. An accumulator that never accumulated still prints. ``MaxAccum<DOUBLE>`` prints
   negative DBL_MAX and ``MaxAccum<DATETIME>`` prints the epoch. Both are "no value",
   and both are catastrophic if they reach a language model: it will report, with a
   citation, that the card was first seen in that region in 1970.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Final

#: Marks a Savanna workspace that is waking from idle rather than a failure.
_COLD_START_MARKER: Final[str] = "starting workspace"


def is_cold_start(body: str) -> bool:
    """True when this response body is Savanna's "waking up" page, not an answer.

    An idle workspace answers the first request with an HTML holding page instead
    of JSON and is ready roughly 45 seconds later. Lives here, not on the
    repository, because :class:`~sentinel.graph.token.TokenManager` hits the same
    wall when it mints a token against a sleeping workspace.
    """
    head = body.lstrip()[:512].lower()
    return head.startswith("<html") and _COLD_START_MARKER in body.lower()


class ResponseNormalizer:
    """Flattens, de-aliases and sanitises a REST++ response body.

    Responsibility: wire format only. It knows nothing about which query produced
    a payload, and it never decides that a value is interesting.
    Collaborators: held by :class:`~sentinel.graph.tigergraph.TigerGraphRestRepository`,
    which runs :meth:`normalize` over every ``results`` list before anyone sees it.
    """

    #: What an empty ``MaxAccum<DOUBLE>`` prints. TigerGraph emits 16 significant
    #: digits, one more than float64 can hold, so ``json.loads`` folds it to -inf
    #: rather than to -DBL_MAX. Both forms are checked; so is the +DBL_MAX mirror
    #: an empty ``MinAccum<DOUBLE>`` would produce.
    NEG_INF: Final[float] = -1.797693134862316e308

    #: What an empty ``DATETIME`` accumulator prints.
    EPOCH_SENTINEL: Final[str] = "1970-01-01 00:00:00"

    _DBL_MAX: Final[float] = sys.float_info.max

    # ── the three primitives ─────────────────────────────────────────────────

    def flatten(self, blocks: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
        """Merge TigerGraph's list-of-blocks into one dict.

        A block is usually single-key, but one ``PRINT`` statement with several
        comma-separated terms emits them together, so blocks are merged by item.
        """
        out: dict[str, Any] = {}
        for block in blocks or ():
            out.update(block)
        return out

    def strip_alias(self, row: Mapping[str, Any]) -> dict[str, Any]:
        """``'T.txn_id'`` -> ``'txn_id'``; ``'C2.@shared'`` -> ``'shared'``."""
        return {self.bare_key(key): value for key, value in row.items()}

    def sanitize(self, value: Any) -> Any:
        """Replace both empty-accumulator sentinels with ``None``, recursively.

        Structure and every other value are preserved exactly: ``0`` is a real
        count and ``""`` is a real "no device record", and neither is missing data.
        """
        if isinstance(value, Mapping):
            return {key: self.sanitize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.sanitize(item) for item in value]
        if isinstance(value, bool):
            return value
        if isinstance(value, float):
            return None if self._is_numeric_sentinel(value) else value
        if isinstance(value, str):
            return None if self._is_epoch_sentinel(value) else value
        return value

    # ── the two compositions the repository and the tools use ────────────────

    def normalize(self, blocks: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
        """Flatten then sanitise: what ``run_query`` hands back to a caller."""
        return self.sanitize(self.flatten(blocks))  # type: ignore[no-any-return]

    def rows(self, value: Any) -> list[dict[str, Any]]:
        """The ``attributes`` of a printed vertex set, alias-stripped.

        Takes what a block holds — a list of ``{v_id, v_type, attributes}`` rows —
        and returns just the attribute dicts. ``v_id`` is left behind because every
        vertex type in this graph also carries its id as a named attribute.
        """
        if not isinstance(value, (list, tuple)):
            return []
        out: list[dict[str, Any]] = []
        for row in value:
            if not isinstance(row, Mapping):
                continue
            attributes = row.get("attributes", row)
            if isinstance(attributes, Mapping):
                out.append(self.strip_alias(attributes))
        return out

    # ── internals ────────────────────────────────────────────────────────────

    @staticmethod
    def bare_key(key: str) -> str:
        """Drop the GSQL alias prefix and the accumulator marker from one key."""
        alias, dot, attribute = key.partition(".")
        return (attribute if dot else alias).lstrip("@")

    @classmethod
    def _is_numeric_sentinel(cls, value: float) -> bool:
        # NaN is left alone: it is not something an empty accumulator produces,
        # and silently nulling it would hide a genuine arithmetic fault.
        return math.isinf(value) or abs(value) >= cls._DBL_MAX

    @classmethod
    def _is_epoch_sentinel(cls, value: str) -> bool:
        return value.strip().replace("T", " ") == cls.EPOCH_SENTINEL
