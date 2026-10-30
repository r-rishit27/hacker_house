"""Case write-back: the graph is where the memory lives, not a local file.

The brief asks for two separate things and it is worth keeping them apart. The
answer *file* is the submission. The `FraudCase` *vertex* is the memory — it is
what makes the nineteenth case able to retrieve the third one, inside the same
run, through the same retriever that reaches the bank's 5,565 closed cases.
Writing only the file would satisfy the grader's file check and quietly fail the
requirement it exists to demonstrate.

Eight edge types connect a case to the entities it is about. They are written
even when the run's verdict is `legitimate`: an exonerated card that is alerted
again next month should retrieve the reason it was cleared.

Two things from v1's ``sentinel/memory.py`` are deleted rather than ported:

- **`_customer_id` / `_card_id` smuggled into the answer dict.** Private keys
  inside a scored payload. :class:`CaseWriteRequest` carries them as fields.
- **`_device_key_for_label`**, which string-interpolated a label into an
  interpreted GSQL scan of all 9,704 `DeviceProfile` vertices. The device key is
  already in the sweep's results; nothing needs to go looking for it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sentinel.domain.answer import AnswerFile
from sentinel.graph.repository import (
    EdgeUpsert,
    GraphRepository,
    VertexUpsert,
)
from sentinel.rag.embeddings import EmbeddingService

logger = logging.getLogger(__name__)

#: `Case` is a reserved GSQL keyword, so the vertex type is `FraudCase` and its
#: ids are prefixed to keep them distinct from the bank's own `CC-NNNN` rows.
CASE_ID_PREFIX = "CASE-"

#: The bank's closed investigations. Anything in `similar_prior_cases` carrying
#: this prefix is a `ClosedCase`; anything carrying `CASE-` is one of ours.
CLOSED_CASE_PREFIX = "CC-"

#: TigerGraph's DATETIME wire format.
_TS = "%Y-%m-%d %H:%M:%S"


def graph_case_id(case_id: str) -> str:
    """``HHG-003`` -> ``CASE-HHG-003``. Idempotent, so it can be applied twice."""
    return case_id if case_id.startswith(CASE_ID_PREFIX) else f"{CASE_ID_PREFIX}{case_id}"


def is_closed_case(case_id: str) -> bool:
    """Whether an id names one of the bank's 5,565 closed investigations."""
    return case_id.startswith(CLOSED_CASE_PREFIX)


@dataclass(frozen=True, slots=True)
class CaseWriteRequest:
    """Everything needed to write one case to the graph.

    The entity ids are fields rather than keys inside ``answer`` because the
    answer file is the graded artefact and an extra key in it is a failure — its
    models are declared ``extra="forbid"`` precisely so that smuggling is a
    type error rather than a habit.
    """

    case_id: str
    answer: AnswerFile
    alert_id: str
    customer_id: str
    card_id: str
    device_keys: Sequence[str] = ()
    #: `ClosedCase` ids this investigation actually rested on.
    cites_closed_cases: Sequence[str] = ()
    #: `FraudCase` ids Sentinel wrote earlier in this same run.
    cites_cases: Sequence[str] = ()
    #: `PolicyDoc` ids for the rules the policy engine cited. GraphRAG's
    #: structural footprint: `APPLIED_RULE` edges make "which rule decided this"
    #: a graph question.
    applied_rules: Sequence[str] = ()
    created_at: datetime | None = None
    created_by: str = "sentinel"


@dataclass(frozen=True, slots=True)
class CaseWriteResult:
    """What the write-back actually put in the graph."""

    graph_case_id: str
    vertices_upserted: int
    edges_upserted: int
    edge_types: tuple[str, ...]
    embedded: bool

    @property
    def ok(self) -> bool:
        return self.vertices_upserted > 0

    def as_dict(self) -> dict[str, object]:
        return {
            "graph_case_id": self.graph_case_id,
            "vertices_upserted": self.vertices_upserted,
            "edges_upserted": self.edges_upserted,
            "edge_types": list(self.edge_types),
            "embedded": self.embedded,
        }


@dataclass
class CaseMemoryStore:
    """Writes a finished case into the graph, and reads cases back out.

    Responsibility: the vertex, its eight edge types and its embedding. It makes
    no fraud decision and reads nothing from the answer that the answer does not
    already state.
    Collaborators: ``GraphRepository`` for the write; ``EmbeddingService`` for
    ``FraudCase.emb``, which is what lets a later case in the same run retrieve
    this one by similarity rather than only by shared card.
    """

    graph: GraphRepository
    embeddings: EmbeddingService | None = None
    #: Present so a dry run can exercise every other part of the write path.
    dry_run: bool = False
    _written: list[str] = field(default_factory=list)

    @property
    def written(self) -> tuple[str, ...]:
        """Graph ids written by this store, in order. The run's own memory."""
        return tuple(self._written)

    async def write(self, request: CaseWriteRequest) -> CaseWriteResult:
        """Upsert the case vertex and every edge, in one round trip."""
        vid = graph_case_id(request.case_id)
        answer = request.answer
        case = answer.case

        emb = await self._embed(case.summary)
        attrs: dict[str, Any] = {
            "source_alert": request.alert_id,
            "customer_id": request.customer_id,
            "card_id": request.card_id,
            "status": case.status.value,
            "verdict": case.verdict.value,
            "fraud_probability": case.fraud_probability,
            "pattern": case.pattern.value,
            "pattern_description": case.pattern_description,
            "exposure_usd": case.exposure_usd,
            "n_affected_txns": len(case.affected_txn_ids),
            "summary": case.summary,
            "initial_actions": _actions(answer, "initial"),
            "final_actions": _actions(answer, "final"),
            "sar_filed": answer.sar.file,
            "stop_reason": answer.stop_reason,
            "created_at": (request.created_at or datetime.now()).strftime(_TS),
            "created_by": request.created_by,
        }
        if emb is not None:
            attrs["emb"] = emb

        edges = self._edges(vid, request)
        if self.dry_run:
            logger.info(
                "dry run: would write %s with %d edges across %d types",
                vid,
                len(edges),
                len({e.etype for e in edges}),
            )
            return CaseWriteResult(
                graph_case_id=vid,
                vertices_upserted=0,
                edges_upserted=0,
                edge_types=_types(edges),
                embedded=emb is not None,
            )

        result = await self.graph.upsert_batch(
            vertices=[VertexUpsert("FraudCase", vid, attrs)], edges=edges
        )
        self._written.append(vid)
        logger.info(
            "wrote %s: %d vertices, %d edges across %s",
            vid,
            result.vertices,
            result.edges,
            ", ".join(_types(edges)) or "no types",
        )
        return CaseWriteResult(
            graph_case_id=vid,
            vertices_upserted=result.vertices,
            edges_upserted=result.edges,
            edge_types=_types(edges),
            embedded=emb is not None,
        )

    # ── the edges ────────────────────────────────────────────────────────────

    @staticmethod
    def _edges(vid: str, request: CaseWriteRequest) -> list[EdgeUpsert]:
        """The eight edge types, deduplicated and with empty ids dropped.

        An empty id would be accepted by REST++ as a vertex literally named ``""``
        and would then fail the graph identity check on the answer file, which is
        a confusing way to learn that a list had a blank in it.
        """
        case = request.answer.case
        plan: list[tuple[str, str, Sequence[str]]] = [
            ("CASE_INVOLVES", "Transaction", case.affected_txn_ids),
            ("CASE_ON_CARD", "Card", [request.card_id]),
            ("CASE_CONNECTED_TO", "Card", case.connected_card_ids),
            ("CITES", "ClosedCase", request.cites_closed_cases),
            ("CITES_CASE", "FraudCase", [graph_case_id(c) for c in request.cites_cases]),
            ("IMPLICATES", "DeviceProfile", request.device_keys),
            ("APPLIED_RULE", "PolicyDoc", request.applied_rules),
            ("FROM_ALERT", "Alert", [request.alert_id]),
        ]
        edges: list[EdgeUpsert] = []
        seen: set[tuple[str, str]] = set()
        for etype, dst_type, ids in plan:
            for dst in ids:
                dst = str(dst).strip()
                if not dst or (etype, dst) in seen:
                    continue
                seen.add((etype, dst))
                edges.append(EdgeUpsert(etype, "FraudCase", vid, dst_type, dst))
        return edges

    async def _embed(self, summary: str) -> list[float] | None:
        if self.embeddings is None or not summary.strip():
            return None
        try:
            return await self.embeddings.embed_one(summary)
        except Exception:  # a missing vector must never lose the case
            logger.warning("case summary could not be embedded; writing without emb", exc_info=True)
            return None


def _types(edges: Sequence[EdgeUpsert]) -> tuple[str, ...]:
    """Edge types present, in first-written order."""
    return tuple(dict.fromkeys(edge.etype for edge in edges))


def _actions(answer: AnswerFile, phase: str) -> str:
    """``CREATE_CASE|WARN_CUSTOMER`` — the format `ClosedCase.actions_taken` uses.

    Matching the bank's own encoding matters: a query that unions Sentinel's
    cases with the bank's closed ones should not need two parsers.
    """
    recs = getattr(answer.next_best_actions, phase)
    return "|".join(rec.action.value for rec in recs)
