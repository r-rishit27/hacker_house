"""Where an investigation says what it is doing.

The orchestrator has exactly one output channel besides its return value, and
this is it. That matters because the same stream serves three consumers: the
console's live timeline, the persisted journal a run is replayed from, and the
audit trace a judge reads. One channel, so they cannot drift.

The domain layer only knows the ABC. Persisting to SQLite and fanning out over
SSE belong to the API, which subclasses it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

#: The event vocabulary, fixed. The frontend's generated contract mirrors it and
#: a name added here without one added there is a silent gap in the timeline.
EVENT_TYPES: tuple[str, ...] = (
    "run.started",
    "step.started",
    "step.completed",
    "tool.called",
    "evidence.posted",
    "retrieval.completed",
    "pattern.rejected",
    "policy.evaluated",
    "evidence.requested",
    "llm.completed",
    "budget.updated",
    "verdict.reached",
    "case.written",
    "case.write_failed",
    "validation.completed",
    "run.completed",
    "run.failed",
)

TERMINAL_EVENTS: frozenset[str] = frozenset({"run.completed", "run.failed"})


@dataclass(frozen=True, slots=True)
class Event:
    """One thing that happened, with the step it happened in."""

    type: str
    payload: dict[str, Any]
    step: int | None = None


class EventEmitter(ABC):
    """The orchestrator's only output channel.

    Responsibility: accept an event. Implementations decide whether that means
    dropping it, collecting it, or writing it to a journal before publishing.
    """

    @abstractmethod
    def emit(self, event_type: str, payload: dict[str, Any], step: int | None = None) -> None: ...

    def _check(self, event_type: str) -> None:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"'{event_type}' is not one of the {len(EVENT_TYPES)} event types")


class NullEmitter(EventEmitter):
    """Emits nowhere. The default, so a unit test constructs no plumbing."""

    def emit(self, event_type: str, payload: dict[str, Any], step: int | None = None) -> None:
        self._check(event_type)


@dataclass
class CollectingEmitter(EventEmitter):
    """Keeps everything in order, for tests and for the offline CLI.

    The CLI uses this rather than a no-op so a terminal run still produces the
    trace file the UI reads, without the API being up.
    """

    events: list[Event] = field(default_factory=list)

    def emit(self, event_type: str, payload: dict[str, Any], step: int | None = None) -> None:
        self._check(event_type)
        self.events.append(Event(event_type, dict(payload), step))

    def of_type(self, event_type: str) -> list[Event]:
        return [event for event in self.events if event.type == event_type]

    def types(self) -> list[str]:
        return [event.type for event in self.events]

    @property
    def trajectory(self) -> list[float]:
        """The probability after each posting — the sparkline the UI draws."""
        return [float(event.payload["p_after"]) for event in self.of_type("evidence.posted")]
