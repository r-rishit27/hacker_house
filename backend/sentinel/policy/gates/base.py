"""The gate contract: a hard bar that runs after the rules have spoken.

Collaborators: ``RecommendationSet`` (which a gate edits in place) and
``CaseState`` (which it may only read). ``PolicyEngine`` owns the pipeline and
collects one ``GateOutcome`` per gate for the ``policy.evaluated`` event.

A gate exists because the rules in section 5.2 can produce a contradictory set —
R2 adds ``BLOCK_CARD`` on a denial while R7 forbids blocking the same card. The
rules stay readable and the contradiction is resolved once, here, where it is
individually testable and individually citable in the UI.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from sentinel.domain.enums import Action
from sentinel.policy.state import CaseState

if TYPE_CHECKING:  # pragma: no cover - import cycle: the engine owns the set
    from sentinel.policy.engine import RecommendationSet


@dataclass(frozen=True, slots=True)
class GateOutcome:
    """What one gate did, in the words the UI renders.

    ``removed`` and ``added`` are named because the analyst's question is never
    "did a gate run" but "which bar stopped the block". A gate that changed
    nothing returns an outcome with all three empty, and ``fired`` is False.
    """

    gate: str
    removed: tuple[Action, ...] = ()
    added: tuple[Action, ...] = ()
    reason: str = ""

    @property
    def fired(self) -> bool:
        return bool(self.removed or self.added or self.reason)

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "removed": [action.value for action in self.removed],
            "added": [action.value for action in self.added],
            "reason": self.reason,
        }


class PolicyGate(ABC):
    """One hard bar. Reads the state, edits the recommendation set, reports.

    Subclasses set ``name`` — it is the identifier the SSE payload and the UI use,
    so it is stable and rule-shaped (``R1NoWeakBlock``), not a sentence.
    """

    name: ClassVar[str] = "Gate"

    @abstractmethod
    def apply(self, recs: RecommendationSet, state: CaseState) -> GateOutcome:
        """Enforce the bar. Must be idempotent: running it twice changes nothing."""

    def _silent(self) -> GateOutcome:
        """The outcome for a gate whose condition did not hold."""
        return GateOutcome(self.name)


@dataclass(frozen=True, slots=True)
class RouteChange:
    """A route that a rule body got wrong and ``RouteRecomputeGate`` corrected."""

    action: Action
    was: str
    now: str


@dataclass(slots=True)
class GateLog:
    """The outcomes of one pipeline run, in order.

    Collaborator: ``PolicyEngine``, which hands it to the emitter. Kept as an
    object rather than a bare list so ``fired()`` has one definition.
    """

    outcomes: list[GateOutcome] = field(default_factory=list)

    def record(self, outcome: GateOutcome) -> None:
        self.outcomes.append(outcome)

    def fired(self) -> list[GateOutcome]:
        return [outcome for outcome in self.outcomes if outcome.fired]

    def as_list(self) -> list[dict[str, Any]]:
        return [outcome.as_dict() for outcome in self.outcomes]
