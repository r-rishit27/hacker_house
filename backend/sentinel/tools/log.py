"""What one graph call produced, and the record of every call in one investigation."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass(frozen=True)
class ToolResult:
    """One graph call: its citation, its parsed facts, and whether it worked.

    Responsibility: carry a single tool invocation's outcome without
    interpreting it. A failed call is still a result — ``ok=False`` with an
    ``error`` — because the agent must be able to see that a question was asked
    and went unanswered, and the trace must show it.
    Collaborators: produced by :class:`~sentinel.tools.registry.ToolRegistry`,
    recorded by :class:`QueryLog`, consumed by the feature extractor.
    """

    tool: str
    ref: str
    data: BaseModel | None = None
    entity_ids: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    ok: bool = True
    error: str | None = None

    @classmethod
    def failed(
        cls,
        tool: str,
        ref: str,
        error: str,
        elapsed_s: float = 0.0,
        entity_ids: list[str] | None = None,
    ) -> ToolResult:
        return cls(
            tool=tool,
            ref=ref,
            data=None,
            entity_ids=entity_ids or [],
            elapsed_s=elapsed_s,
            ok=False,
            error=error,
        )


class QueryLog:
    """Every graph call made during ONE investigation.

    Responsibility: an ordered, append-only trace of tool calls, for the answer
    file's citations and for the run budget.
    Collaborators: constructed by the orchestrator, injected into
    :class:`~sentinel.tools.registry.ToolRegistry`.

    One log per investigation, always. v1's ``GraphTools.__init__`` defaulted to
    a log it made itself, so a batch of twenty concurrent cases would have merged
    into one trace; there is deliberately no default here.
    """

    def __init__(self) -> None:
        self._calls: list[ToolResult] = []

    @property
    def calls(self) -> list[ToolResult]:
        """A snapshot. ``record`` is the only way to add to the log."""
        return list(self._calls)

    def record(self, result: ToolResult) -> ToolResult:
        self._calls.append(result)
        return result

    @property
    def count(self) -> int:
        return len(self._calls)

    @property
    def total_seconds(self) -> float:
        return sum(call.elapsed_s for call in self._calls)

    def refs(self) -> list[str]:
        return [call.ref for call in self._calls]

    def failures(self) -> list[ToolResult]:
        return [call for call in self._calls if not call.ok]
