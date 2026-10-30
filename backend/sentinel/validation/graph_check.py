"""The half of validation that needs the live graph.

A made-up id scores zero, so every transaction, card and closed case an answer
names is confirmed to exist before the file is accepted, and the exposure figure
is re-added from the graph's own amounts.

Two things here are deliberately unlike ``eval/validate.py``:

1. **A failed lookup is not a missing vertex.** v1 wrapped every call in
   ``except Exception`` and reported ``"{vtype} '{vid}' does not exist in the
   graph"``, so one expired token turned a correct submission into an invalid
   one. Only :class:`VertexNotFound` means missing here; ``GraphUnavailable``
   propagates as what it is and the caller retries.
2. **Ids are fetched in batches.** v1 issued one REST call per id — a 40-id
   answer meant 40 round trips to a workspace that takes 45 s to wake.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from sentinel.domain.errors import VertexNotFound
from sentinel.validation.validator import ValidationReport, as_mapping

_CLOSED_CASE_PREFIX = "CC-"
_CARD_SUFFIX = re.compile(r"-K\d+$")

#: The `doc_id` prefixes `CorpusBuilder` writes. A `source: "document"` evidence
#: item names one of these, and an id naming a chunk that was never ingested is
#: exactly the kind of unverifiable citation this checker exists to catch.
_POLICY_DOC_PREFIXES = ("POL-", "PAT-", "GLO-", "KNOW-", "REG-")


@runtime_checkable
class VertexReader(Protocol):
    """The two repository operations this checker needs.

    ``GraphRepository`` satisfies it structurally. Stated as a Protocol so that
    validation depends on the reads it makes rather than on the whole repository.
    """

    async def get_vertex(self, vtype: str, vid: str) -> dict[str, Any] | None: ...

    async def get_vertices(self, vtype: str, ids: Sequence[str]) -> dict[str, dict[str, Any]]: ...


class GraphIdentityChecker:
    """Confirms against TigerGraph that an answer names only real things.

    Responsibility: id existence for Transaction, Card, ClosedCase and FraudCase;
    the exposure arithmetic; and that ``written_to_graph`` really did write a
    ``FraudCase``. It judges nothing about fraud and applies no policy.
    Collaborators: a ``VertexReader`` (the graph repository) for batched reads,
    and the ``ValidationReport`` it adds findings to — the same report
    ``AnswerValidator`` fills, so one report describes the whole answer.
    """

    #: Amounts are stored as floats; a cent of drift over a long episode is not an error.
    EXPOSURE_TOLERANCE_USD = 0.02

    def __init__(self, graph_repository: VertexReader) -> None:
        self._graph = graph_repository

    async def check(self, answer: object, report: ValidationReport) -> None:
        """Run every graph-backed check, adding findings to ``report``.

        Raises ``GraphUnavailable`` (or any other ``GraphError`` that is not a
        missing vertex) rather than recording it as a defect in the answer.
        """
        payload = as_mapping(answer)
        case = payload.get("case")
        if not isinstance(case, Mapping):
            return

        wanted = self._collect(case)
        found: dict[str, dict[str, dict[str, Any]]] = {}
        for vtype, ids in wanted.items():
            found[vtype] = await self._fetch(vtype, sorted(ids))
            for missing in sorted(ids - found[vtype].keys()):
                report.add_error(
                    "vertex_not_found",
                    f"{vtype}:{missing}",
                    f"{vtype} '{missing}' does not exist in the graph",
                )

        self._check_exposure(case, found.get("Transaction", {}), report)
        await self._check_written_to_graph(case, report)

    def _collect(self, case: Mapping[str, Any]) -> dict[str, set[str]]:
        """Every id in the answer, bucketed by the vertex type it must exist as."""
        wanted: dict[str, set[str]] = {
            "Transaction": set(),
            "Card": set(),
            "ClosedCase": set(),
            "PolicyDoc": set(),
        }
        wanted["Transaction"].update(self._strings(case.get("affected_txn_ids")))
        first = case.get("first_suspicious_txn_id")
        if isinstance(first, str) and first:
            wanted["Transaction"].add(first)
        wanted["Card"].update(self._strings(case.get("connected_card_ids")))
        wanted["ClosedCase"].update(self._strings(case.get("similar_prior_cases")))

        evidence = case.get("evidence")
        if isinstance(evidence, list):
            for item in evidence:
                if not isinstance(item, Mapping):
                    continue
                for entity_id in self._strings(item.get("entity_ids")):
                    vtype = self._classify(entity_id)
                    if vtype is not None:
                        wanted[vtype].add(entity_id)
        return wanted

    def _classify(self, entity_id: str) -> str | None:
        """Which vertex type an evidence id refers to, or ``None`` for ids with no vertex.

        A heuristic, kept from v1 because it reads the id shapes this dataset
        actually uses: ``CC-2817`` is a closed case, ``C08623-K2`` a card, a bare
        run of digits a transaction. Customers (``C08623``), billing regions
        (``330.0``) and device-profile labels are left alone: the first two are
        not ids this validator is asked to confirm, and a device profile is a
        composite label rather than a vertex key. ``POL-R7`` and its siblings
        are `PolicyDoc` chunks, checked because a cited rule that is not in the
        corpus is a citation nobody can follow.
        """
        if entity_id.startswith(_CLOSED_CASE_PREFIX):
            return "ClosedCase"
        if entity_id.startswith(_POLICY_DOC_PREFIXES):
            return "PolicyDoc"
        if _CARD_SUFFIX.search(entity_id):
            return "Card"
        if entity_id.isdigit():
            return "Transaction"
        return None

    async def _fetch(self, vtype: str, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        try:
            return await self._graph.get_vertices(vtype, ids)
        except VertexNotFound:
            # A repository may answer a batch of entirely unknown ids this way; the
            # caller below reports each id as missing. Every other GraphError — an
            # expired token, a cold workspace — propagates untouched.
            return {}

    def _check_exposure(
        self,
        case: Mapping[str, Any],
        transactions: Mapping[str, Mapping[str, Any]],
        report: ValidationReport,
    ) -> None:
        """``exposure_usd`` must equal the summed absolute amounts of the episode."""
        affected = self._strings(case.get("affected_txn_ids"))
        claimed = case.get("exposure_usd")
        if not affected or not isinstance(claimed, int | float) or isinstance(claimed, bool):
            return

        total = 0.0
        for txn_id in affected:
            amount = self._amount(transactions.get(txn_id))
            if amount is None:
                report.add_warning(
                    "exposure_unchecked",
                    "case.exposure_usd",
                    f"exposure was not re-added: transaction '{txn_id}' has no readable amount in the graph",
                )
                return
            total += abs(amount)

        if abs(total - float(claimed)) > self.EXPOSURE_TOLERANCE_USD:
            report.add_error(
                "exposure_mismatch",
                "case.exposure_usd",
                f"exposure_usd is {float(claimed):,.2f} but the {len(affected)} affected "
                f"transactions sum to {total:,.2f}",
            )

    def _amount(self, vertex: Mapping[str, Any] | None) -> float | None:
        """Read ``amt`` from either a normalised vertex or a raw REST++ one."""
        if vertex is None:
            return None
        attributes = vertex.get("attributes")
        source: Mapping[str, Any] = attributes if isinstance(attributes, Mapping) else vertex
        value = source.get("amt")
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return float(value)

    async def _check_written_to_graph(
        self, case: Mapping[str, Any], report: ValidationReport
    ) -> None:
        if not case.get("written_to_graph"):
            return
        case_id = case.get("graph_case_id")
        if not isinstance(case_id, str) or not case_id:
            return  # LegitimateCaseRule already reports the empty id.
        try:
            vertex = await self._graph.get_vertex("FraudCase", case_id)
        except VertexNotFound:
            vertex = None
        if vertex is None:
            report.add_error(
                "graph_case_not_found",
                "case.graph_case_id",
                f"written_to_graph is true but FraudCase '{case_id}' is not in the graph",
            )

    def _strings(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str) and item]
