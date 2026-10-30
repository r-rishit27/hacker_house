"""The control flow of an investigation.

Deliberately a plain loop over an ordered list of steps rather than a framework.
Three things follow from that and all three are graded: the step numbering is
ours (``asked_after_step`` is a field in the answer file), the budget is checked
between steps rather than by a callback, and the event emission order is fixed
so the console can be built against it before the agent exists.

The API depends on ``InvestigationOrchestrator``, never on the concrete class.
That is what lets the frontend work against a scripted run from day one and what
makes the demo replayable from a journal rather than from a live model call.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import ValidationError

from sentinel.agents.agents.assessment import AssessmentAgent, DevilsAdvocateAgent
from sentinel.agents.agents.narration import NarrationAgent
from sentinel.agents.agents.planner import PlannerAgent
from sentinel.agents.assembler import AnswerAssembler
from sentinel.agents.budget import BudgetGuard, RunBudget
from sentinel.agents.context import InvestigationContext
from sentinel.agents.emitter import EventEmitter, NullEmitter
from sentinel.agents.steps import (
    AssessStep,
    DecideStep,
    InvestigationStep,
    NarrateStep,
    PlanStep,
    RecallStep,
    RequestEvidenceStep,
    ScopeStep,
    StopTestStep,
    SweepStep,
    WriteStep,
)
from sentinel.config.settings import Settings
from sentinel.domain.alert import Alert
from sentinel.domain.answer import AnswerFile
from sentinel.domain.errors import AnswerInvalid, BudgetExceeded, SentinelError
from sentinel.evidence.extractor import FeatureExtractor
from sentinel.evidence.ledger import EvidenceLedger
from sentinel.evidence.table import EvidenceLikelihoodTable
from sentinel.graph.repository import GraphRepository
from sentinel.llm.client import LlmClient
from sentinel.memory.store import CaseMemoryStore
from sentinel.policy.engine import PolicyEngine, build_policy_engine
from sentinel.policy.stopping import StoppingPolicy
from sentinel.rag.retriever import GraphRagRetriever
from sentinel.tools.log import QueryLog
from sentinel.tools.registry import ToolRegistry
from sentinel.validation.validator import AnswerValidator, ValidationReport

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InvestigationResult:
    """What one run produced."""

    answer: AnswerFile
    validation: ValidationReport
    context: InvestigationContext

    @property
    def ok(self) -> bool:
        return self.validation.ok


class InvestigationOrchestrator(ABC):
    """Runs one alert to an answer.

    Implementations: ``SentinelOrchestrator`` for the real agent, and — in the
    API layer — a scripted one that replays a fixture and a replay one that
    reads a recorded journal. The API imports only this.
    """

    @abstractmethod
    async def run(
        self, alert: Alert, emitter: EventEmitter | None = None
    ) -> InvestigationResult: ...


@dataclass
class SentinelOrchestrator(InvestigationOrchestrator):
    """The ten steps, in order, over one shared context.

    Responsibility: sequence the steps, number them, enforce the budget between
    them, and emit the run-level events. It makes no fraud decision of its own.
    Collaborators: every step; ``AnswerAssembler`` at the end; ``AnswerValidator``
    before anything is returned as good.
    """

    settings: Settings
    repository: GraphRepository
    llm: LlmClient
    table: EvidenceLikelihoodTable
    engine: PolicyEngine = field(default_factory=build_policy_engine)
    assembler: AnswerAssembler = field(default_factory=AnswerAssembler)
    validator: AnswerValidator = field(default_factory=AnswerValidator)
    #: Supplied by the caller. Absent, the run is a dry one: every step but the
    #: write-back happens, so a unit test and a `--no-write` run take the same
    #: path through the other nine.
    memory: CaseMemoryStore | None = None
    #: Absent, `recall` degrades to the structural lookup and no `document`
    #: evidence is produced. Present, retrieval is hybrid and grounded.
    retriever: GraphRagRetriever | None = None
    steps: Sequence[InvestigationStep] = ()

    def __post_init__(self) -> None:
        if not self.steps:
            self.steps = self.default_steps()

    def default_steps(self) -> tuple[InvestigationStep, ...]:
        """The published pipeline. `write` appears only when a store was given,
        so a dry run takes the identical path through the other nine steps."""
        write: tuple[InvestigationStep, ...] = (
            (WriteStep(self.memory, self.assembler),) if self.memory is not None else ()
        )
        return (
            ScopeStep(),
            PlanStep(PlannerAgent(llm=self.llm)),
            SweepStep(),
            RecallStep(self.retriever),
            AssessStep(
                AssessmentAgent(llm=self.llm),
                DevilsAdvocateAgent(llm=self.llm),
            ),
            StopTestStep(StoppingPolicy(self.engine.config)),
            RequestEvidenceStep(),
            DecideStep(self.engine),
            NarrateStep(NarrationAgent(llm=self.llm)),
            *write,
        )

    async def run(self, alert: Alert, emitter: EventEmitter | None = None) -> InvestigationResult:
        emitter = emitter or NullEmitter()
        ctx = self.build_context(alert, emitter)

        emitter.emit(
            "run.started",
            {
                "case_id": alert.alert_id,
                "trigger_type": alert.trigger_type.value,
                "trigger_text": alert.trigger_text,
                "flagged_txn_id": alert.flagged_txn_id,
                "card_id": alert.card_id,
                "customer_id": alert.customer_id,
                # None on nine of the twenty: the alert's risk_score is the
                # missing-numeric sentinel, not a score.
                "alert_risk_score": alert.risk_score,
                # Where the probability starts: the trigger type's fitted prior,
                # never the model's score.
                "prior": round(ctx.ledger.prior, 4),
                "mode": "live",
                "orchestrator": type(self).__name__,
                "budget": ctx.budget.snapshot().as_dict(),
            },
        )

        try:
            for step in self.steps:
                if step.skip(ctx):
                    continue
                number = ctx.counter.next()
                emitter.emit(
                    "step.started",
                    {
                        "step": number,
                        "name": step.name,
                        "title": step.title,
                        "agent": type(step).__name__,
                    },
                    step=number,
                )
                calls_before = ctx.tools.log.count
                postings_before = len(ctx.ledger.postings)
                await step.run(ctx)
                emitter.emit(
                    "step.completed",
                    {
                        "step": number,
                        "name": step.name,
                        "tool_calls_in_step": ctx.tools.log.count - calls_before,
                        "postings_in_step": len(ctx.ledger.postings) - postings_before,
                    },
                    step=number,
                )
                ctx.guard.check_clock()
        except BudgetExceeded as exc:
            emitter.emit(
                "run.failed",
                {
                    "code": "budget_exceeded",
                    "message": exc.message,
                    "step": ctx.counter.current,
                    "recoverable": False,
                },
            )
            raise
        except SentinelError as exc:
            emitter.emit(
                "run.failed",
                {
                    "code": exc.code,
                    "message": exc.message,
                    "step": ctx.counter.current,
                    "recoverable": False,
                },
            )
            raise

        try:
            answer = self.assembler.assemble(ctx)
        except ValidationError as exc:
            # The answer models enforce invariants the steps cannot see, and a
            # violation here is a `pydantic.ValidationError` — neither a
            # `BudgetExceeded` nor a `SentinelError`, so it escaped the loop's
            # except clauses with no `run.failed` and no file. A run that
            # cannot produce a valid answer must still say so on the stream.
            message = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
            )
            emitter.emit(
                "run.failed",
                {
                    "code": "answer_invalid",
                    "message": message,
                    "step": ctx.counter.current,
                    "recoverable": False,
                },
            )
            raise AnswerInvalid(message, case_id=alert.alert_id) from exc

        emitter.emit("verdict.reached", _verdict_payload(ctx, answer), step=ctx.counter.current)

        report = self.validator.validate(answer.model_dump(mode="json"))
        emitter.emit(
            "validation.completed",
            {
                "ok": report.ok,
                "errors": [f.as_dict() for f in report.errors],
                "warnings": [f.as_dict() for f in report.warnings],
                "graph_checked": False,
            },
            step=ctx.counter.current,
        )
        emitter.emit(
            "run.completed",
            {
                "status": "completed",
                "tool_calls": answer.tool_calls,
                "tokens": answer.tokens,
                "latency_s": answer.latency_s,
                "validation_ok": report.ok,
                "answer_url": f"/api/cases/{alert.alert_id}/answer",
            },
        )
        return InvestigationResult(answer=answer, validation=report, context=ctx)

    def build_context(self, alert: Alert, emitter: EventEmitter) -> InvestigationContext:
        """One ledger, one log, one budget per investigation. Never shared."""
        budget = RunBudget(settings=self.settings, meter=self.llm.meter)
        return InvestigationContext(
            alert=alert,
            tools=ToolRegistry(self.repository, QueryLog()),
            ledger=EvidenceLedger(self.table, alert.trigger_type),
            extractor=FeatureExtractor(self.table),
            guard=BudgetGuard(budget),
            emitter=emitter,
        )


def _verdict_payload(ctx: InvestigationContext, answer: AnswerFile) -> dict[str, object]:
    return {
        "status": answer.case.status.value,
        "verdict": answer.case.verdict.value,
        "fraud_probability": answer.case.fraud_probability,
        "pattern": answer.case.pattern.value,
        "pattern_description": answer.case.pattern_description,
        "exposure_usd": answer.case.exposure_usd,
        "affected_txn_ids": answer.case.affected_txn_ids,
        "connected_card_ids": answer.case.connected_card_ids,
        "connected_device_profiles": answer.case.connected_device_profiles,
        "similar_prior_cases": answer.case.similar_prior_cases,
        "summary": answer.case.summary,
        "stop_reason": answer.stop_reason,
        "trajectory": ctx.ledger.trajectory,
    }
