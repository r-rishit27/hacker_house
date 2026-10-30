"""Citation strings.

Every ``ref`` that ever reaches an answer file is built here and nowhere else.
An analyst reading a case must be able to re-run any single line of it, so the
citation is formed at the call site of the fact, once, and carried unchanged
through the ledger into ``AnswerFile.case.evidence[].ref``.

The ``query:`` format is transcribed from v1's ``_fmt_ref`` and is load-bearing:
the strings appear verbatim in the graded answer files under ``cases/``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any


class EvidenceRef:
    """The single place a citation string is built.

    Responsibility: formatting only. It performs no lookups and holds no state.
    Collaborators: :class:`~sentinel.tools.registry.GraphTool` builds query refs
    from resolved arguments; the evidence ledger, the retriever and the
    simulator build the other three kinds.
    """

    @staticmethod
    def query(name: str, params: Mapping[str, Any]) -> str:
        """``query:card_baseline(card_id=C08623-K2)``.

        ``params`` are the human-readable arguments, not the GSQL wire names:
        an analyst re-runs ``card_baseline`` with a card id, not with ``c_in``.
        """
        inner = ", ".join(f"{key}={_render(value)}" for key, value in params.items())
        return f"query:{name}({inner})"

    @staticmethod
    def alert(alert_id: str) -> str:
        """``alert:HHG-003`` — a fact taken from the trigger itself."""
        return f"alert:{alert_id}"

    @staticmethod
    def evidence_request(index: int) -> str:
        """``evidence_request:1`` — 1-based, matching the answer file's array order."""
        return f"evidence_request:{index}"

    @staticmethod
    def document(doc_id: str, section: str = "") -> str:
        """``doc:POL-R5#verify_before_block`` — GraphRAG's visible footprint."""
        return f"doc:{doc_id}#{section}" if section else f"doc:{doc_id}"


def _render(value: Any) -> str:
    """Datetimes render in the graph's own format; everything else as ``str``."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)
