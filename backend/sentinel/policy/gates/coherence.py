"""The three bars that keep an action list internally coherent.

Collaborators: ``RecommendationSet`` and, for ``RouteRecomputeGate``, the set's
own ``RoutingTable``. None of these enforces a numbered rule; they enforce that
the list as a whole can be read by an analyst without contradicting itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sentinel.domain.enums import Action
from sentinel.policy.gates.base import GateOutcome, PolicyGate
from sentinel.policy.state import CaseState

if TYPE_CHECKING:  # pragma: no cover - import cycle: the engine owns the set
    from sentinel.policy.engine import RecommendationSet

_ENFORCEMENT = (Action.BLOCK_CARD, Action.BLOCK_ALL_CARDS, Action.FILE_REPORT)


class ReportNeedsCaseGate(PolicyGate):
    """Section 3a: "A report always has a case behind it."

    The case is inserted at position 0, not appended: the list is in execution
    order, and the case exists before the report that cites it.
    """

    name: ClassVar[str] = "ReportNeedsCase"

    def apply(self, recs: RecommendationSet, state: CaseState) -> GateOutcome:
        if not recs.has(Action.FILE_REPORT) or recs.has(Action.CREATE_CASE):
            return self._silent()
        recs.insert_first(Action.CREATE_CASE, "3a: a report always has a case behind it", state)
        return GateOutcome(
            self.name,
            added=(Action.CREATE_CASE,),
            reason="3a: a report always has a case behind it",
        )


class CloseNoFraudExclusivityGate(PolicyGate):
    """``CLOSE_NO_FRAUD`` cannot sit beside a block or a regulatory filing.

    Closing an alert as "no fraud" while blocking the card is not a judgement
    call, it is two contradictory answers — and the evaluator reads them as one.
    """

    name: ClassVar[str] = "CloseNoFraudExclusivity"

    def apply(self, recs: RecommendationSet, state: CaseState) -> GateOutcome:
        if not recs.has(Action.CLOSE_NO_FRAUD):
            return self._silent()
        conflicts = [action for action in _ENFORCEMENT if recs.has(action)]
        if not conflicts:
            return self._silent()
        removed = recs.remove_any(Action.CLOSE_NO_FRAUD)
        named = ", ".join(action.value for action in conflicts)
        return GateOutcome(
            self.name,
            removed=removed,
            reason=f"CLOSE_NO_FRAUD cannot coexist with {named}",
        )


class RouteRecomputeGate(PolicyGate):
    """Every route is recomputed from the routing table, never trusted from above.

    A rule body that names its own route is the failure this gate exists for:
    ``BLOCK_CARD`` on a $2,600 exposure is ``L2``, and a hand-written ``L1``
    would send an unapproved block straight through.
    """

    name: ClassVar[str] = "RouteRecompute"

    def apply(self, recs: RecommendationSet, state: CaseState) -> GateOutcome:
        changes = recs.recompute_routes(state)
        if not changes:
            return self._silent()
        corrected = "; ".join(
            f"{change.action.value} {change.was} to {change.now}" for change in changes
        )
        return GateOutcome(
            self.name,
            reason=f"routes recomputed from the routing table: {corrected}",
        )
