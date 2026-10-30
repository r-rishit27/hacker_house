"""Loop guards, promoted from convention to enforced settings.

A tool-calling agent without ceilings will, on some case, call the same query
forty times or request evidence twice. The second one matters most: the answer
format has exactly ``initial`` and ``final``, so a second evidence round has
nowhere to go and the file becomes unrepresentable.

Exceeding a ceiling ends the run as ``budget_exceeded`` with the partial trace
kept. It never silently truncates, because a quietly shortened investigation
looks exactly like a thorough one that found nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic

from sentinel.config.settings import Settings
from sentinel.domain.errors import BudgetExceeded
from sentinel.llm.client import CostMeter, MeterReading


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    """What the SSE ``budget.updated`` payload and the UI's budget bar carry."""

    tool_calls: int
    max_tool_calls: int
    evidence_rounds: int
    max_evidence_rounds: int
    tokens: int
    max_tokens: int
    usd: float
    max_usd: float
    elapsed_s: float
    max_elapsed_s: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "tool_calls": self.tool_calls,
            "max_tool_calls": self.max_tool_calls,
            "evidence_rounds": self.evidence_rounds,
            "max_evidence_rounds": self.max_evidence_rounds,
            "tokens": self.tokens,
            "max_tokens": self.max_tokens,
            "usd": round(self.usd, 4),
            "max_usd": self.max_usd,
            "elapsed_s": round(self.elapsed_s, 2),
            "max_elapsed_s": self.max_elapsed_s,
        }


@dataclass
class RunBudget:
    """The ceilings for one investigation, and what it has spent.

    Collaborators: ``CostMeter`` supplies the token and dollar totals, so there
    is one place tokens are counted and ``answer.tokens`` cannot disagree with
    the budget.
    """

    settings: Settings
    meter: CostMeter = field(default_factory=CostMeter)
    tool_calls: int = 0
    evidence_rounds: int = 0
    started_at: float = field(default_factory=monotonic)
    #: What the meter read when this run began. One ``LlmClient`` serves the
    #: whole batch, so everything below is a difference against this.
    baseline: MeterReading = field(default_factory=MeterReading)

    def __post_init__(self) -> None:
        if self.baseline == MeterReading():
            self.baseline = self.meter.reading()

    @property
    def elapsed_s(self) -> float:
        return monotonic() - self.started_at

    @property
    def tokens(self) -> int:
        """Tokens *this run* spent. What ``answer.tokens`` reports."""
        return self.meter.tokens - self.baseline.tokens

    @property
    def usd(self) -> float:
        """Dollars this run spent."""
        return self.meter.usd - self.baseline.usd

    @property
    def llm_calls(self) -> int:
        return self.meter.calls - self.baseline.calls

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            tool_calls=self.tool_calls,
            max_tool_calls=self.settings.max_tool_calls_per_run,
            evidence_rounds=self.evidence_rounds,
            max_evidence_rounds=self.settings.max_evidence_rounds,
            tokens=self.tokens,
            max_tokens=self.settings.max_tokens_per_run,
            usd=self.usd,
            max_usd=self.settings.max_usd_per_run,
            elapsed_s=self.elapsed_s,
            max_elapsed_s=float(self.settings.max_run_seconds),
        )


@dataclass
class BudgetGuard:
    """Refuses the call that would breach a ceiling, before it is made.

    Responsibility: one ``check`` per resource, each raising with the ceiling it
    hit named. It never trims a plan silently — the orchestrator decides what to
    do about a refusal, and the trace records that the run stopped rather than
    finished.
    """

    budget: RunBudget

    @property
    def settings(self) -> Settings:
        return self.budget.settings

    def check_tool_call(self) -> None:
        """Called before dispatching, so the refused call never reaches the graph."""
        if self.budget.tool_calls >= self.settings.max_tool_calls_per_run:
            raise BudgetExceeded(
                f"tool-call ceiling reached ({self.settings.max_tool_calls_per_run})",
                ceiling="tool_calls",
                snapshot=self.budget.snapshot().as_dict(),
            )

    def record_tool_call(self) -> None:
        self.budget.tool_calls += 1

    def check_evidence_round(self) -> None:
        """The answer format has exactly `initial` and `final`. One round, ever."""
        if self.budget.evidence_rounds >= self.settings.max_evidence_rounds:
            raise BudgetExceeded(
                "evidence-request ceiling reached "
                f"({self.settings.max_evidence_rounds}); the answer format has only "
                "initial and final",
                ceiling="evidence_rounds",
                snapshot=self.budget.snapshot().as_dict(),
            )

    def record_evidence_round(self) -> None:
        self.budget.evidence_rounds += 1

    def check_spend(self) -> None:
        """Tokens, dollars and wall clock, checked together before an LLM call."""
        if self.budget.tokens >= self.settings.max_tokens_per_run:
            raise BudgetExceeded(
                f"token ceiling reached ({self.settings.max_tokens_per_run})",
                ceiling="tokens",
                snapshot=self.budget.snapshot().as_dict(),
            )
        if self.budget.usd >= self.settings.max_usd_per_run:
            raise BudgetExceeded(
                f"spend ceiling reached (${self.settings.max_usd_per_run:.2f})",
                ceiling="usd",
                snapshot=self.budget.snapshot().as_dict(),
            )
        self.check_clock()

    def check_clock(self) -> None:
        if self.budget.elapsed_s >= self.settings.max_run_seconds:
            raise BudgetExceeded(
                f"wall-clock ceiling reached ({self.settings.max_run_seconds}s)",
                ceiling="elapsed_s",
                snapshot=self.budget.snapshot().as_dict(),
            )

    def remaining_tool_calls(self) -> int:
        return max(self.settings.max_tool_calls_per_run - self.budget.tool_calls, 0)
