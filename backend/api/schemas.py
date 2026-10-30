"""The wire contract: one Pydantic model per request and response the API serves.

This module exists so that three things cannot drift apart — what the API
returns, what ``frontend/src/lib/generated/contract.ts`` declares, and what the
domain actually computed. Every field below is either a field of an object
``sentinel`` already produces (an ``Alert``, an ``AnswerFile``, a ``Posting``, a
``RetrievalHit``, a ``BudgetSnapshot``) or a column of a table in ``api.store``.
Nothing is invented at the boundary, because a field invented here is a field
with no one to compute it.

Four decisions are encoded in the models rather than left to the routers, each
of them a bug that would otherwise be found in the browser:

**A case renders before it has an answer.** All twenty alerts are in the queue
from the first request and most of them have no answer file for most of a
benchmark run, so every answer-derived field on :class:`CaseSummary` is
optional and :meth:`CaseSummary.of` takes ``answer=None``. FR-27.

**Routes are recomputed, never echoed.** :class:`ActionRow` carries the route
``RoutingTable`` returns for the action at *this* case's exposure and, when the
answer file claimed a different one, both — so drift is badged rather than
obeyed. HLD ADR-6.

**The domain enums are imported, never redeclared.** ``Action``, ``Route``,
``Verdict`` and the rest are the members of ``sentinel.domain.enums``. The
enums declared here are the operational vocabularies the domain has no opinion
about: a run's status, an execution's outcome, an approval's decision. HLD
ADR-7.

**Models read off rows and dataclasses rather than being copied into.** The
operational models set ``from_attributes``, so ``ExecutionRecord.model_validate
(row)`` is the whole mapping and a column renamed in ``api.store.models``
fails here instead of silently serving a stale shape.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api import __version__
from api.sse.journal import ENVELOPE_VERSION
from sentinel.agents.emitter import EVENT_TYPES, TERMINAL_EVENTS
from sentinel.agents.steps import (
    AssessStep,
    DecideStep,
    NarrateStep,
    PlanStep,
    RecallStep,
    RequestEvidenceStep,
    ScopeStep,
    StopTestStep,
    SweepStep,
    WriteStep,
)
from sentinel.domain.alert import DEFAULT_ALERT_STATUS, Alert
from sentinel.domain.answer import ActionRecommendation, AnswerFile, SarReport
from sentinel.domain.enums import (
    APPROVERS,
    FIXED_ROUTES,
    Action,
    CaseStatus,
    CustomerResponse,
    EvidenceSource,
    Pattern,
    RequestType,
    Role,
    Route,
    TriggerType,
    Verdict,
)
from sentinel.evidence.ledger import GROUP_CAP
from sentinel.memory.store import CaseWriteResult
from sentinel.policy.config import DEFAULT_POLICY_CONFIG, PolicyConfig
from sentinel.policy.engine import Recommendation
from sentinel.policy.routing import RoutingTable
from sentinel.rag.retriever import KIND_CLOSED_CASE, KIND_POLICY, KIND_SENTINEL_CASE
from sentinel.tools.registry import TOOL_NAMES

T = TypeVar("T")


class Row(BaseModel):
    """Base for a model read straight off a store row or a domain object.

    Responsibility: carry ``from_attributes`` once instead of on twenty
    classes, so ``model_validate(row)`` is the mapping and there is no
    hand-written field-by-field copy to fall out of date.
    Collaborators: ``api.store.models``' six tables, and the frozen dataclasses
    in ``sentinel`` — ``Posting``, ``Finding``, ``BudgetSnapshot``,
    ``RetrievalHit``.
    """

    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    """``{items, total}`` — the envelope every list endpoint returns.

    Responsibility: one page of rows plus the unpaged total, so the queue can
    render "20 of 20" without a second count request.
    Collaborators: the list endpoints; the TypeScript emitter, which turns this
    into ``Page<T>`` and each concrete parametrisation into a named alias.
    """

    items: list[T] = Field(default_factory=list)
    total: int = 0


# ── operational vocabularies ─────────────────────────────────────────────────
#
# These are the API's own enums. They are not in ``sentinel.domain.enums``
# because the domain has no opinion about them: an investigation does not know
# it is "queued", and the fraud policy has nothing to say about idempotent
# replays. Every value is transcribed from the column comments in
# ``api/store/models.py``, which is where they are enforced.


class RunStatus(str, Enum):
    """Where one investigation is. ``cancelled`` is distinct from ``failed``."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunMode(str, Enum):
    """How a run produces its events.

    ``replay`` re-reads a journal at demo speed and ``scripted`` plays a
    fixture; both exist so the console can be shown without credentials, and
    both are recorded on the run so a trace can never be mistaken for a live
    investigation.
    """

    LIVE = "live"
    REPLAY = "replay"
    SCRIPTED = "scripted"


class Phase(str, Enum):
    """Which of the answer file's two recommendation lists an action came from."""

    INITIAL = "initial"
    FINAL = "final"


class ExecutionOutcome(str, Enum):
    """What one execution attempt did. Denials and failures are rows, not absences."""

    EXECUTED = "executed"
    DENIED = "denied"
    FAILED = "failed"
    REPLAYED = "replayed"


class ApprovalStatus(str, Enum):
    """Where one approval is. Only ``pending`` is unique per case, action and phase."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalDecision(str, Enum):
    """The verb in ``POST /api/approvals/{id}/decision``."""

    APPROVE = "approve"
    REJECT = "reject"


class DenialPolicy(str, Enum):
    """What ``POST /api/actions/execute`` does when the route needs an approval.

    ``enqueue`` is the default because the exit test is one click from blocked
    to inbox: the 403 carries the approval it just created. ``reject`` opts
    out, for a caller that wants the refusal and nothing else.
    """

    ENQUEUE = "enqueue"
    REJECT = "reject"


class CaseSort(str, Enum):
    """Queue orderings. ``opened_at`` is the default and the run order.

    Sorting by ``case_id`` is *not* chronological — the pack's ids and its
    ``opened_at`` values disagree, and case memory only compounds forwards.
    """

    OPENED_AT = "opened_at"
    CASE_ID = "case_id"
    PROBABILITY = "probability"
    EXPOSURE = "exposure"


class BatchOrder(str, Enum):
    """The order a benchmark batch runs its cases in.

    ``chronological`` is the only defensible default: HHG-019 may retrieve the
    ``FraudCase`` Sentinel wrote for HHG-003, and must not be able to retrieve
    one from a case that had not happened yet.
    """

    CHRONOLOGICAL = "chronological"
    CASE_ID = "case_id"
    AS_LISTED = "as_listed"


class ServiceStatus(str, Enum):
    """Liveness. ``degraded`` is the honest answer while the graph workspace wakes."""

    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


class RetrievalKind(str, Enum):
    """What a retrieved item is. The three kinds ``GraphRagRetriever`` emits.

    The values come from the retriever's own constants rather than being
    retyped: ``sentinel_case`` is Sentinel's own memory and ``closed_case`` is
    one of the bank's 5,565, and the console renders them differently.
    """

    CLOSED_CASE = KIND_CLOSED_CASE
    POLICY_DOC = KIND_POLICY
    SENTINEL_CASE = KIND_SENTINEL_CASE


class StepName(str, Enum):
    """The ten investigation steps, named by the step classes themselves.

    Read off ``sentinel.agents.steps`` rather than transcribed, because these
    names reach the UI timeline and the persisted trace — a rename there with
    no rename here is a silent gap in both.
    """

    SCOPE = ScopeStep.name
    PLAN = PlanStep.name
    SWEEP = SweepStep.name
    RECALL = RecallStep.name
    ASSESS = AssessStep.name
    STOP_TEST = StopTestStep.name
    REQUEST_EVIDENCE = RequestEvidenceStep.name
    DECIDE = DecideStep.name
    NARRATE = NarrateStep.name
    WRITE = WriteStep.name


class GraphNodeKind(str, Enum):
    """The vertex types ``graph/schema.gsql`` defines, verbatim.

    The canvas encodes each kind differently — a ``FraudCase`` is Sentinel's
    own memory and is drawn in the accent colour, a ``ClosedCase`` is the
    bank's and is not — so the kind is a closed vocabulary, not a label.
    """

    CUSTOMER = "Customer"
    CARD = "Card"
    TRANSACTION = "Transaction"
    DEVICE_PROFILE = "DeviceProfile"
    BILLING_REGION = "BillingRegion"
    EMAIL_DOMAIN = "EmailDomain"
    PRODUCT_CODE = "ProductCode"
    CLOSED_CASE = "ClosedCase"
    FRAUD_CASE = "FraudCase"
    ALERT = "Alert"
    POLICY_DOC = "PolicyDoc"


# ── shared value objects ─────────────────────────────────────────────────────


class BudgetSnapshot(Row):
    """The ceilings for one run and what it has spent, as the budget bar reads it.

    Responsibility: mirror ``sentinel.agents.budget.BudgetSnapshot`` field for
    field so ``model_validate(budget.snapshot())`` is the whole conversion.
    Collaborators: the ``budget.updated`` event, ``run.started``'s payload and
    :class:`RunDetail`.
    """

    tool_calls: int = 0
    max_tool_calls: int = 0
    evidence_rounds: int = 0
    max_evidence_rounds: int = 0
    tokens: int = 0
    max_tokens: int = 0
    usd: float = 0.0
    max_usd: float = 0.0
    elapsed_s: float = 0.0
    max_elapsed_s: float = 0.0


class ValidationFinding(Row):
    """One defect in one answer file, addressed by its JSON path.

    Mirrors ``sentinel.validation.validator.Finding``: a stable ``code`` and a
    path, because "invalid submission" alone tells an analyst nothing about
    which of the 47 fields to fix.
    """

    code: str
    path: str
    message: str


class ValidationBody(Row):
    """``GET /api/cases/{case_id}/validation`` — what one validation pass found.

    Responsibility: carry the report and say whether the graph half ran.
    ``graph_checked`` is false for the pure pass, which is the one the
    orchestrator runs before writing and the one CI runs with no credentials.
    Collaborators: ``AnswerValidator`` and ``GraphIdentityChecker``.
    """

    ok: bool = True
    errors: list[ValidationFinding] = Field(default_factory=list)
    warnings: list[ValidationFinding] = Field(default_factory=list)
    graph_checked: bool = False


class PostingView(Row):
    """One evidence posting and what it did to the probability.

    Mirrors ``sentinel.evidence.ledger.Posting`` — including ``group`` and
    ``capped``, which are what let the trace show *why* the third phrasing of a
    device finding moved the number by nothing.
    """

    feature: str
    present: bool
    lr: float
    log_lr: float
    p_before: float
    p_after: float
    claim: str
    ref: str
    source: EvidenceSource = EvidenceSource.GRAPH
    entity_ids: list[str] = Field(default_factory=list)
    capped: bool = False
    group: str = ""
    step: int | None = Field(
        default=None,
        description="The step this was posted in. Null when replayed from a trace file.",
    )


class GateOutcome(Row):
    """What one policy gate did, in the words the UI renders.

    Mirrors ``sentinel.policy.gates.base.GateOutcome``. The analyst's question
    is never "did a gate run" but "which bar stopped the block", so the removed
    and added actions are named rather than counted.
    """

    gate: str
    removed: list[Action] = Field(default_factory=list)
    added: list[Action] = Field(default_factory=list)
    reason: str = ""


class SarSummary(BaseModel):
    """``sar.file`` and its reason, as the policy evaluation carries them.

    Both fields travel together because they are one decision: the reason is a
    graded field on the answer and is populated in the not-filing branch too.
    """

    file: bool = False
    reason: str = ""


class StopSummary(BaseModel):
    """Policy section 6's answer at one moment, with the sentence behind it."""

    should_stop: bool = False
    reason: str = ""


class RetrievalHit(Row):
    """One retrieved item, with the evidence of how it was found.

    Mirrors ``sentinel.rag.retriever.RetrievalHit``. ``provenance`` is the edge
    the expansion actually traversed — it is what makes the memory tab show
    graph retrieval rather than a vector store standing next to a graph.
    Every field but ``id`` carries a default because the structural-only path
    emits ids alone.
    """

    id: str
    kind: RetrievalKind = RetrievalKind.CLOSED_CASE
    score: float = 0.0
    title: str = ""
    snippet: str = ""
    ref: str = ""
    provenance: dict[str, Any] = Field(default_factory=dict)


# ── SSE payloads ─────────────────────────────────────────────────────────────
#
# One model per entry in ``EVENT_TYPES``. They are declared here rather than
# left as loose dicts for two reasons: ``GET /api/investigations/{id}/
# events.json`` serves them, and the TypeScript emitter reads them to type the
# ``SentinelEvent`` union the console's timeline switches on.
#
# Fields the orchestrator does not always send carry a default, so a journalled
# payload always validates; the generated TypeScript still declares them
# required, because a consumer reading one back always sees a value.


class RunStartedPayload(BaseModel):
    """The alert, the prior it bought and the ceilings the run starts under."""

    case_id: str
    trigger_type: TriggerType
    trigger_text: str = ""
    flagged_txn_id: str = ""
    card_id: str = ""
    customer_id: str = ""
    alert_risk_score: float | None = Field(
        default=None,
        description=(
            "Null on nine of the twenty: the alert carried the -1 missing-numeric "
            "sentinel, not a score. The model's score is on the transaction."
        ),
    )
    prior: float = Field(
        default=0.0,
        description="Where the probability starts — the trigger type's fitted prior.",
    )
    mode: RunMode = RunMode.LIVE
    orchestrator: str = ""
    budget: BudgetSnapshot = Field(default_factory=BudgetSnapshot)


class StepStartedPayload(BaseModel):
    """A numbered stage began. ``agent`` is the class that runs it."""

    step: int
    name: StepName
    title: str = ""
    agent: str = ""


class StepCompletedPayload(BaseModel):
    """A stage ended, with what it cost and what it found."""

    step: int
    name: StepName
    elapsed_s: float = 0.0
    tool_calls_in_step: int = 0
    postings_in_step: int = 0


class ToolCalledPayload(BaseModel):
    """One graph query, with the citation it produced.

    ``ref`` is the string that lands in ``answer.case.evidence[].ref``, so the
    timeline row and the graded evidence item are provably the same call.
    """

    step: int
    tool: str
    ref: str = ""
    params: dict[str, str | int | bool] = Field(default_factory=dict)
    entity_ids: list[str] = Field(default_factory=list)
    elapsed_s: float = 0.0
    ok: bool = True
    error: str | None = None
    summary: str = ""


class EvidencePostedPayload(BaseModel):
    """One finding priced as a likelihood ratio, and the probability either side.

    The same thirteen fields as :class:`PostingView` minus the ones only a
    stored trace has — this is the live form, and the console's evidence list
    is built from it before any answer file exists.
    """

    step: int
    feature: str
    present: bool = Field(
        default=True,
        description="False means the feature's absence likelihood was posted. Absence is evidence.",
    )
    group: str = ""
    claim: str = ""
    source: EvidenceSource = EvidenceSource.GRAPH
    ref: str = ""
    entity_ids: list[str] = Field(default_factory=list)
    lr: float = 1.0
    log_lr: float = 0.0
    p_before: float = 0.0
    p_after: float = 0.0
    capped: bool = Field(
        default=False,
        description="The group cap clipped this posting — the UI draws a hatched bar end.",
    )


class RetrievalCompletedPayload(BaseModel):
    """What one retrieval asked for and what came back."""

    step: int
    kind: RetrievalKind = RetrievalKind.CLOSED_CASE
    strategy: str = Field(
        default="",
        description=(
            'Free text, not a closed set: the recall step emits "structural" for the '
            'card lookup and "hybrid (vector + structural, RRF)" for the fused one.'
        ),
    )
    query: str = ""
    hits: list[RetrievalHit] = Field(default_factory=list)


class PolicyEvaluatedPayload(BaseModel):
    """One pass of the policy engine over one state.

    Emitted twice per case — once per phase — and the two payloads side by side
    are the initial-to-final diff the console renders.
    """

    step: int
    phase: Phase
    fraud_probability: float = Field(
        default=0.0,
        description=(
            "The state's probability, not the ledger's. On the initial phase they "
            "differ: the state holds the snapshot taken before the requested evidence."
        ),
    )
    exposure_usd: float = 0.0
    verdict: Verdict = Verdict.UNCERTAIN
    independent_support: int = Field(
        default=0,
        description="Distinct ledger groups that moved by more than 0.05. Policy 6 counts these.",
    )
    recommendations: list[ActionRecommendation] = Field(default_factory=list)
    gates_applied: list[GateOutcome] = Field(default_factory=list)
    sar: SarSummary = Field(default_factory=SarSummary)
    stop: StopSummary = Field(default_factory=StopSummary)


class EvidenceRequestedPayload(BaseModel):
    """The one round of evidence the agent may ask for, and what it assumed back."""

    step: int = Field(
        default=0,
        description="The same counter that fills answer.evidence_requests[].asked_after_step.",
    )
    request_index: int = 1
    type: RequestType
    rationale: str = ""
    assumed_response: str = ""
    assumption_basis: list[str] = Field(
        default_factory=list,
        description="The query refs the assumption rests on — never a guess.",
    )
    branch: CustomerResponse
    log_lr_applied: float = 0.0


class LlmCompletedPayload(BaseModel):
    """One model call, its tokens and its cost.

    ``fallback`` is true when the call failed and the agent degraded to its
    deterministic output: the trace has to show that the step ran without a
    model rather than quietly looking the same.
    """

    step: int
    purpose: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    usd: float = 0.0
    elapsed_s: float = 0.0
    fallback: bool = False
    error: str = ""


class VerdictReachedPayload(BaseModel):
    """The whole case shape, plus the trajectory that got there."""

    step: int | None = None
    status: CaseStatus
    verdict: Verdict
    fraud_probability: float
    pattern: Pattern
    pattern_description: str = ""
    exposure_usd: float = 0.0
    affected_txn_ids: list[str] = Field(default_factory=list)
    connected_card_ids: list[str] = Field(default_factory=list)
    connected_device_profiles: list[str] = Field(default_factory=list)
    similar_prior_cases: list[str] = Field(default_factory=list)
    summary: str = ""
    stop_reason: str = ""
    trajectory: list[float] = Field(
        default_factory=list,
        description="The prior followed by the probability after each posting.",
    )


class CaseWrittenPayload(BaseModel):
    """The case vertex and its edges landed in the graph — Sentinel's own memory."""

    step: int | None = None
    graph_case_id: str
    vertices_upserted: int = 0
    edges_upserted: int = 0
    edge_types: list[str] = Field(default_factory=list)
    embedded: bool = False
    answer_path: str = ""


class CaseWriteFailedPayload(BaseModel):
    """The write-back failed and the run continued.

    A separate event rather than a ``run.failed``: the answer file is the
    submission and it is already valid, so losing the memory write is a warning
    on the case, not the end of the investigation.
    """

    step: int | None = None
    case_id: str
    code: str
    message: str = ""


class ValidationCompletedPayload(BaseModel):
    """The answer was validated. ``graph_checked`` says whether ids were proven."""

    step: int | None = None
    ok: bool
    errors: list[ValidationFinding] = Field(default_factory=list)
    warnings: list[ValidationFinding] = Field(default_factory=list)
    graph_checked: bool = False


class RunCompletedPayload(BaseModel):
    """Terminal. The connection closes after this one."""

    status: RunStatus = RunStatus.COMPLETED
    tool_calls: int = 0
    tokens: int = Field(default=0, description="Never 0 on an agent run.")
    latency_s: float = 0.0
    validation_ok: bool = False
    answer_url: str = ""


class RunFailedPayload(BaseModel):
    """Terminal. ``code`` is the domain error's own code, not an HTTP status.

    Values seen in practice: ``budget_exceeded``, ``answer_invalid``,
    ``graph_unavailable``, ``llm_unavailable``, ``vertex_not_found``,
    ``query_failed`` — any ``SentinelError.code``, so this is a string rather
    than a closed union that would go stale the first time one is added.
    """

    code: str
    message: str = ""
    step: int | None = None
    recoverable: bool = False


class PatternRejectedPayload(BaseModel):
    """A pattern the model named and the measurements ruled out.

    Emitted rather than swallowed. The pattern is a graded field and it feeds
    the episode scoper, so a rejection is a thing an analyst should be able to
    see in the timeline — "it said out-of-region use; the card has 42 prior
    transactions in that region" — rather than a value that quietly became
    `none`.
    """

    step: int
    named: Pattern
    contradiction: str
    accepted: Pattern = Pattern.NONE


#: Which payload model belongs to which event name. The TypeScript emitter
#: turns this into the discriminated ``SentinelEvent`` union, and the
#: completeness check below is what makes a new event type impossible to add
#: without a payload for it.
EVENT_PAYLOADS: dict[str, type[BaseModel]] = {
    "run.started": RunStartedPayload,
    "step.started": StepStartedPayload,
    "step.completed": StepCompletedPayload,
    "tool.called": ToolCalledPayload,
    "evidence.posted": EvidencePostedPayload,
    "retrieval.completed": RetrievalCompletedPayload,
    "pattern.rejected": PatternRejectedPayload,
    "policy.evaluated": PolicyEvaluatedPayload,
    "evidence.requested": EvidenceRequestedPayload,
    "llm.completed": LlmCompletedPayload,
    "budget.updated": BudgetSnapshot,
    "verdict.reached": VerdictReachedPayload,
    "case.written": CaseWrittenPayload,
    "case.write_failed": CaseWriteFailedPayload,
    "validation.completed": ValidationCompletedPayload,
    "run.completed": RunCompletedPayload,
    "run.failed": RunFailedPayload,
}

if set(EVENT_PAYLOADS) != set(EVENT_TYPES):  # pragma: no cover - import-time guard
    missing = sorted(set(EVENT_TYPES) - set(EVENT_PAYLOADS))
    extra = sorted(set(EVENT_PAYLOADS) - set(EVENT_TYPES))
    raise RuntimeError(
        f"EVENT_PAYLOADS does not cover EVENT_TYPES; missing {missing}, unknown {extra}"
    )


class EventEnvelope(Row):
    """One journalled event as it goes on the wire.

    Responsibility: mirror ``api.sse.journal.SseEnvelope`` so the replay
    endpoint and the stream serve the identical shape — a client that
    reconnects must not have to parse two.
    Collaborators: ``EventJournal`` produces it; ``format_sse`` writes ``seq``
    as the SSE ``id:`` line.
    """

    v: int = ENVELOPE_VERSION
    seq: int = Field(
        description="== the SSE id: field. Monotonic per run from 1, so replay is seq > n."
    )
    run_id: str
    case_id: str
    type: str
    at: str
    step: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventPage(BaseModel):
    """``GET /api/investigations/{run_id}/events.json`` — the journal, paged.

    ``next`` is the ``seq`` to ask for next, or null at the end of the journal;
    it is the same cursor as ``Last-Event-ID``, so a client can switch between
    polling and streaming without translating.
    """

    events: list[EventEnvelope] = Field(default_factory=list)
    next: int | None = None


# ── health, readiness and the contract itself ────────────────────────────────


class Health(BaseModel):
    """``GET /api/health`` — the process is up. No dependency is touched."""

    status: ServiceStatus = ServiceStatus.OK
    version: str = __version__
    uptime_s: float = 0.0


class ReadyCheck(BaseModel):
    """One dependency, probed. ``detail`` carries the failure, not a log line."""

    name: str
    ok: bool
    detail: str = ""
    elapsed_ms: float = 0.0


class Ready(BaseModel):
    """``GET /api/ready`` — 503 when any check fails.

    Separate from health because the TigerGraph workspace auto-stops and takes
    about 45 s to come back: the process is fine, the system is not, and a
    load balancer must be able to tell those apart.
    """

    ok: bool = False
    checks: list[ReadyCheck] = Field(default_factory=list)


class MetaEnums(BaseModel):
    """Every closed vocabulary the console renders, served from the domain.

    Responsibility: let a client populate a filter without hard-coding a list
    that would become the third copy. HLD ADR-7.
    """

    actions: list[Action] = Field(default_factory=lambda: list(Action))
    routes: list[Route] = Field(default_factory=lambda: list(Route))
    roles: list[Role] = Field(default_factory=lambda: list(Role))
    verdicts: list[Verdict] = Field(default_factory=lambda: list(Verdict))
    case_statuses: list[CaseStatus] = Field(default_factory=lambda: list(CaseStatus))
    patterns: list[Pattern] = Field(default_factory=lambda: list(Pattern))
    evidence_sources: list[EvidenceSource] = Field(default_factory=lambda: list(EvidenceSource))
    request_types: list[RequestType] = Field(default_factory=lambda: list(RequestType))
    trigger_types: list[TriggerType] = Field(default_factory=lambda: list(TriggerType))
    customer_responses: list[CustomerResponse] = Field(
        default_factory=lambda: list(CustomerResponse)
    )
    run_statuses: list[RunStatus] = Field(default_factory=lambda: list(RunStatus))
    execution_outcomes: list[ExecutionOutcome] = Field(
        default_factory=lambda: list(ExecutionOutcome)
    )
    approval_statuses: list[ApprovalStatus] = Field(default_factory=lambda: list(ApprovalStatus))
    phases: list[Phase] = Field(default_factory=lambda: list(Phase))


class MetaRouting(BaseModel):
    """The approval table, served rather than reimplemented in the client.

    ``fixed`` holds the thirteen exposure-independent actions. ``BLOCK_CARD``
    is deliberately absent: it is the only action whose route depends on
    exposure, and a client that found it here would stop asking the server.
    """

    fixed: dict[Action, Route] = Field(default_factory=lambda: dict(FIXED_ROUTES))
    approvers: dict[Route, list[Role]] = Field(
        default_factory=lambda: {route: list(roles) for route, roles in APPROVERS.items()}
    )
    block_card_l2_threshold: float = DEFAULT_POLICY_CONFIG.block_card_l2_threshold


class MetaThresholds(Row):
    """Every tunable number in the Fraud Policy, so the UI can show the rule's own figure."""

    block_card_l2_threshold: float = DEFAULT_POLICY_CONFIG.block_card_l2_threshold
    sar_exposure_threshold: float = DEFAULT_POLICY_CONFIG.sar_exposure_threshold
    case_probability_threshold: float = DEFAULT_POLICY_CONFIG.case_probability_threshold
    verify_before_block_threshold: float = DEFAULT_POLICY_CONFIG.verify_before_block_threshold
    escalate_exposure_threshold: float = DEFAULT_POLICY_CONFIG.escalate_exposure_threshold
    r4_escalate_threshold: float = DEFAULT_POLICY_CONFIG.r4_escalate_threshold
    stop_high: float = DEFAULT_POLICY_CONFIG.stop_high
    stop_low: float = DEFAULT_POLICY_CONFIG.stop_low
    min_independent_support: int = DEFAULT_POLICY_CONFIG.min_independent_support
    group_cap: float = Field(
        default=GROUP_CAP,
        description="Total log-odds any one evidence group may contribute, either way.",
    )


class Meta(BaseModel):
    """``GET /api/meta`` — the whole contract, from the objects that own it.

    Responsibility: be the runtime half of ADR-7. The generated TypeScript is
    the build-time half; this endpoint is what proves the running server agrees
    with it.
    Collaborators: ``sentinel.domain.enums``, ``PolicyConfig``, the tool
    catalogue and the event vocabulary — each read, none restated.
    """

    version: str = __version__
    enums: MetaEnums = Field(default_factory=MetaEnums)
    routing: MetaRouting = Field(default_factory=MetaRouting)
    thresholds: MetaThresholds = Field(default_factory=MetaThresholds)
    tools: list[str] = Field(default_factory=lambda: list(TOOL_NAMES))
    events: list[str] = Field(default_factory=lambda: list(EVENT_TYPES))
    terminal_events: list[str] = Field(default_factory=lambda: sorted(TERMINAL_EVENTS))
    steps: list[StepName] = Field(default_factory=lambda: list(StepName))

    @classmethod
    def current(cls, config: PolicyConfig = DEFAULT_POLICY_CONFIG) -> Meta:
        """The contract as this process is configured — the endpoint's whole body."""
        return cls(
            thresholds=MetaThresholds.model_validate(config),
            routing=MetaRouting(block_card_l2_threshold=config.block_card_l2_threshold),
        )


# ── runs ─────────────────────────────────────────────────────────────────────


class RunSummary(Row):
    """One row of ``GET /api/investigations``, straight off the ``run`` table.

    Responsibility: the counters a list needs and nothing that requires a join.
    ``validation_ok`` is three-valued on purpose — null means the run has not
    reached validation, which is different from having failed it.
    Also the 202 body of ``POST /api/investigations/{run_id}/cancel``: what a
    caller wants back from a cancellation is the run's new state, and that is
    this.
    """

    run_id: str
    case_id: str
    status: RunStatus = RunStatus.QUEUED
    step: int = 0
    mode: RunMode = RunMode.LIVE
    orchestrator: str = ""
    started_at: datetime
    finished_at: datetime | None = None
    tool_calls: int = 0
    tokens: int = 0
    usd: float = 0.0
    latency_s: float = 0.0
    validation_ok: bool | None = None
    error_code: str = ""
    batch_id: str | None = None


class RunCounters(BaseModel):
    """What one run spent. The same four numbers the answer file reports."""

    tool_calls: int = 0
    tokens: int = 0
    usd: float = 0.0
    latency_s: float = 0.0


class RunError(Row):
    """Why a run ended early, in the domain's own vocabulary."""

    code: str = ""
    message: str = ""


class RunDetail(Row):
    """``GET /api/investigations/{run_id}`` — one run, with its budget and its error.

    Responsibility: everything a reconnecting console needs before it opens the
    stream, so a page refresh mid-run does not have to replay the journal to
    find out whether the run is still going.
    """

    run_id: str
    case_id: str
    status: RunStatus = RunStatus.QUEUED
    step: int = 0
    mode: RunMode = RunMode.LIVE
    orchestrator: str = ""
    started_at: datetime
    finished_at: datetime | None = None
    counters: RunCounters = Field(default_factory=RunCounters)
    budget: BudgetSnapshot = Field(default_factory=BudgetSnapshot)
    error: RunError | None = None
    events: int = Field(default=0, description="Journalled events so far — the replay length.")


class StartInvestigationRequest(BaseModel):
    """``POST /api/investigations``.

    ``force`` is what makes a second run on a case legal: without it the
    endpoint 409s on ``run_already_active``, which is the behaviour that keeps
    a double-clicked demo from running the same alert twice.
    """

    case_id: str
    mode: RunMode = RunMode.LIVE
    force: bool = False
    orchestrator: str = Field(
        default="",
        description="Named orchestrator to run. Empty means the configured default.",
    )


class RunAccepted(BaseModel):
    """202 from ``POST /api/investigations`` — where to watch it happen.

    Both URLs are returned because the console picks one: ``stream_url`` for a
    live run, ``events_url`` when it would rather poll the journal.
    """

    run_id: str
    case_id: str
    status: RunStatus = RunStatus.QUEUED
    stream_url: str
    events_url: str


# ── actions ──────────────────────────────────────────────────────────────────


class ActionRow(BaseModel):
    """One recommended action with the route the server recomputed for it.

    Responsibility: hold both routes when they disagree. ``route`` is what
    ``RoutingTable`` returns for this action at this case's exposure and is the
    only one any permission check consults; ``answer_route`` is what the answer
    file claimed, kept so the console can badge the drift. HLD ADR-6.
    Collaborators: :meth:`recomputed` builds it from a ``Recommendation``;
    ``RoutePermissionPolicy`` decides ``may_execute``.
    """

    action: Action
    route: Route
    reason: str = ""
    answer_route: Route | None = Field(
        default=None,
        description="The route the answer file stated, when it differs from the recomputed one.",
    )
    route_changed: bool = False
    approvers: list[Role] = Field(default_factory=list)
    may_execute: bool = False
    execution_id: str | None = None
    outcome: ExecutionOutcome | None = None
    approval_id: str | None = None
    approval_status: ApprovalStatus | None = None

    @classmethod
    def recomputed(
        cls,
        recommendation: Recommendation | ActionRecommendation,
        *,
        exposure_usd: float,
        role: Role,
        routing: RoutingTable | None = None,
    ) -> ActionRow:
        """Build a row, asking the routing table rather than trusting the input.

        The recommendation may come from a policy evaluation or from an answer
        file read off disk. Neither is trusted for the route: an answer file
        can be edited and a stale one can be replayed, and the only route with
        authority is the one computed now, at this exposure.
        """
        table = routing or RoutingTable()
        route = table.route_for(recommendation.action, exposure_usd)
        stated = recommendation.route
        return cls(
            action=recommendation.action,
            route=route,
            reason=recommendation.reason,
            answer_route=stated if stated is not route else None,
            route_changed=stated is not route,
            approvers=list(APPROVERS[route]),
            may_execute=role in APPROVERS[route] and route is Route.AUTO,
        )


class ActionPlan(BaseModel):
    """``GET /api/cases/{case_id}/actions`` — both phases, recomputed.

    Responsibility: the initial-to-final diff that is a quarter of the score,
    plus the permission answer for the principal who asked. ``role`` is echoed
    because ``may_execute`` is only meaningful next to the role it was computed
    for, and the console has a role switcher.
    """

    case_id: str
    exposure_usd: float = 0.0
    role: Role = Role.ANALYST
    initial: list[ActionRow] = Field(default_factory=list)
    final: list[ActionRow] = Field(default_factory=list)
    what_changed: str = "nothing"


class ExecuteRequest(BaseModel):
    """``POST /api/actions/execute`` — the one door to every side effect.

    ``phase`` is part of the request because an approval is idempotent on
    ``(case_id, action, phase)``: executing the initial ``VERIFY_WITH_CUSTOMER``
    and the final one are two different acts on the same case.
    """

    case_id: str
    action: Action
    phase: Phase = Phase.FINAL
    payload: dict[str, Any] = Field(default_factory=dict)
    on_denied: DenialPolicy = DenialPolicy.ENQUEUE


class ExecutionRecord(Row):
    """One attempt to execute an action, including the ones that were denied.

    Mirrors the ``action_execution`` table. ``simulated`` is false only for
    ``CREATE_CASE``, which really does write to the graph — an auditor reading
    a row has to be able to tell a demonstration from a change.
    """

    execution_id: str
    at: datetime
    case_id: str
    run_id: str | None = None
    action: Action
    phase: Phase = Phase.FINAL
    route: Route
    actor_id: str
    actor_role: Role
    approval_id: str | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    outcome: ExecutionOutcome
    simulated: bool = True
    request_id: str = ""


# ── approvals ────────────────────────────────────────────────────────────────


class ApprovalItem(Row):
    """One pending or decided approval, as the inbox renders it.

    Mirrors the ``approval`` table and adds the roles that may decide it, which
    is otherwise a lookup the client would have to do against a table it should
    not own.
    """

    approval_id: str
    created_at: datetime
    decided_at: datetime | None = None
    case_id: str
    action: Action
    phase: Phase = Phase.FINAL
    route: Route
    exposure_usd: float = 0.0
    requested_by: str = ""
    requested_role: Role = Role.ANALYST
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: str | None = None
    decided_role: Role | None = None
    note: str = ""
    execution_id: str | None = None
    approvers: list[Role] = Field(default_factory=list)

    @model_validator(mode="after")
    def _approvers_from_route(self) -> ApprovalItem:
        """Derive the approver list; never take the client's word for it.

        Overwritten rather than defaulted, because this is the same question
        the execute endpoint answers with a 403 and the two must agree.
        """
        self.approvers = list(APPROVERS[self.route])
        return self


class ApprovalQueue(BaseModel):
    """``GET /api/approvals`` — a page plus the tab counts beside it.

    The counts are unfiltered totals per status, not counts of ``items``: the
    inbox shows "3 pending" while displaying the approved tab.
    """

    items: list[ApprovalItem] = Field(default_factory=list)
    total: int = 0
    counts: dict[ApprovalStatus, int] = Field(default_factory=dict)


class ApprovalDecisionRequest(BaseModel):
    """``POST /api/approvals/{id}/decision``."""

    decision: ApprovalDecision
    note: str = ""


class ApprovalApproveRequest(BaseModel):
    """``POST /api/approvals/{id}/approve`` — the decision endpoint's one-verb alias."""

    note: str = ""


class ApprovalDecisionResult(BaseModel):
    """The decided approval and, when it was approved, what that executed.

    ``execution`` is null on a rejection and on an approval whose action could
    not be executed; the approval itself is always returned, because the inbox
    has to update either way.
    """

    approval: ApprovalItem
    execution: ExecutionRecord | None = None


# ── audit ────────────────────────────────────────────────────────────────────


class AuditItem(Row):
    """One row of the complete record of everything anyone asked this system to do.

    Mirrors the ``audit`` table. ``route`` is the recomputed route, never the
    one the client sent — that is the difference between an audit log and a
    transcript.
    """

    audit_id: str
    at: datetime
    request_id: str = ""
    actor_id: str
    actor_role: Role
    case_id: str
    run_id: str | None = None
    action: str = Field(
        description=(
            "One of the 14 policy action names for an execution. A plain string "
            "because the log also records mutations that are not policy actions, "
            "such as a manual write-to-graph."
        )
    )
    route: Route
    approval_ref: str | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    outcome: ExecutionOutcome
    simulated: bool = True


# ── cases ────────────────────────────────────────────────────────────────────


class CaseSummary(BaseModel):
    """One row of the queue — an alert, plus whatever has been learned about it.

    Responsibility: render for all twenty alerts from the first request, which
    is why every answer-derived field is optional and defaulted. Nineteen of
    the twenty have no answer for most of a benchmark run, and a queue that
    404s or 500s on them is a queue that cannot be demonstrated. FR-27.
    Collaborators: ``CasePackLoader`` supplies the alert half; the answer file
    and the ``run`` row supply the rest, through :meth:`of`.
    """

    # ── the alert, always present ────────────────────────────────────────────
    case_id: str
    opened_at: datetime
    trigger_type: TriggerType
    trigger_text: str = ""
    flagged_txn_id: str = ""
    card_id: str = ""
    customer_id: str = ""
    alert_risk_score: float | None = Field(
        default=None,
        description="Null on nine of the twenty — the alert carried no score, not a zero.",
    )
    alert_status: str = DEFAULT_ALERT_STATUS

    # ── the answer, when there is one ────────────────────────────────────────
    has_answer: bool = False
    status: CaseStatus | None = None
    verdict: Verdict | None = None
    fraud_probability: float | None = None
    pattern: Pattern | None = None
    exposure_usd: float | None = None
    affected_txn_ids: list[str] = Field(default_factory=list)
    connected_card_ids: list[str] = Field(default_factory=list)
    similar_prior_cases: list[str] = Field(default_factory=list)
    summary: str = ""
    sar_filed: bool | None = None
    final_actions: list[Action] = Field(default_factory=list)
    written_to_graph: bool = False
    graph_case_id: str = ""

    # ── the run, when one has started ────────────────────────────────────────
    run_id: str | None = None
    run_status: RunStatus | None = None

    @classmethod
    def of(
        cls,
        alert: Alert,
        answer: AnswerFile | None = None,
        run: RunSummary | None = None,
    ) -> CaseSummary:
        """Project an alert, and anything known about it, onto one queue row."""
        summary = cls(
            case_id=alert.alert_id,
            opened_at=alert.opened_at,
            trigger_type=alert.trigger_type,
            trigger_text=alert.trigger_text,
            flagged_txn_id=alert.flagged_txn_id,
            card_id=alert.card_id,
            customer_id=alert.customer_id,
            alert_risk_score=alert.risk_score,
            alert_status=alert.status,
            run_id=run.run_id if run else None,
            run_status=run.status if run else None,
        )
        if answer is None:
            return summary
        case = answer.case
        return summary.model_copy(
            update={
                "has_answer": True,
                "status": case.status,
                "verdict": case.verdict,
                "fraud_probability": case.fraud_probability,
                "pattern": case.pattern,
                "exposure_usd": case.exposure_usd,
                "affected_txn_ids": list(case.affected_txn_ids),
                "connected_card_ids": list(case.connected_card_ids),
                "similar_prior_cases": list(case.similar_prior_cases),
                "summary": case.summary,
                "sar_filed": answer.sar.file,
                "final_actions": [rec.action for rec in answer.next_best_actions.final],
                "written_to_graph": case.written_to_graph,
                "graph_case_id": case.graph_case_id,
            }
        )


class TraceStep(BaseModel):
    """One stage of a finished investigation, with the calls it made.

    ``calls`` holds the ``tool.called`` payloads verbatim rather than a
    reduction of them, because the ref on each call is the citation the graded
    evidence item carries and the trace is where an analyst checks that.
    """

    step: int
    name: StepName
    title: str = ""
    agent: str = ""
    elapsed_s: float = 0.0
    tool_calls_in_step: int = 0
    postings_in_step: int = 0
    completed: bool = False
    calls: list[ToolCalledPayload] = Field(default_factory=list)


class TraceBody(BaseModel):
    """``GET /api/cases/{case_id}/trace`` — how the answer was arrived at.

    Responsibility: the three things the answer format has nowhere to put — the
    step trace, the postings and the probability trajectory — beside the three
    counters it does. ADR-8: the answer file stays the only submission
    artefact, and this is where everything the UI needs lives instead.
    Collaborators: the event journal, or the trace file a CLI run wrote.
    """

    case_id: str
    run_id: str = ""
    steps: list[TraceStep] = Field(default_factory=list)
    postings: list[PostingView] = Field(default_factory=list)
    trajectory: list[float] = Field(default_factory=list)
    tool_calls: int = 0
    tokens: int = 0
    latency_s: float = 0.0


class WriteToGraphRequest(BaseModel):
    """``POST /api/cases/{case_id}/write-to-graph``.

    ``force`` re-writes a case already in the graph. The write is an upsert, so
    this is safe; the flag exists so an accidental second click 409s instead.
    """

    force: bool = False


class WriteToGraphResult(BaseModel):
    """What the write-back actually put in the graph.

    ``embedded`` matters on its own: a case vertex written without its
    embedding is invisible to the vector half of retrieval, so a later case can
    only find it structurally.
    """

    graph_case_id: str
    vertices: int = 0
    edges: int = 0
    edge_types: list[str] = Field(default_factory=list)
    embedded: bool = False

    @classmethod
    def of(cls, result: CaseWriteResult) -> WriteToGraphResult:
        """The store's result in the endpoint's wording (``vertices``, not ``vertices_upserted``)."""
        return cls(
            graph_case_id=result.graph_case_id,
            vertices=result.vertices_upserted,
            edges=result.edges_upserted,
            edge_types=list(result.edge_types),
            embedded=result.embedded,
        )


class SarBody(BaseModel):
    """``GET /api/cases/{case_id}/sar`` — 200 even when ``file`` is false.

    A 404 for a case that decided *not* to file would hide ``sar.reason``,
    which is a graded field: the decision not to file is an answer, not an
    absence.
    """

    case_id: str
    sar: SarReport
    rendered: str = Field(
        default="",
        description="The plain-text filing ``sar.txt`` serves, so the UI needs no second call.",
    )


class RetrievalBreakdown(BaseModel):
    """The two independent rankings behind one retrieval, before fusion.

    Shown side by side on purpose: vector finds a case that *reads* like this
    one, structural finds a case that *is connected* to it, and the memory tab
    is only convincing when both are visible.
    """

    structural: list[RetrievalHit] = Field(default_factory=list)
    vector: list[RetrievalHit] = Field(default_factory=list)


class MemoryBody(BaseModel):
    """``GET /api/cases/{case_id}/memory`` — what this case remembered, and from where.

    Responsibility: separate the bank's 5,565 closed investigations from the
    cases Sentinel itself wrote earlier in the same run. That distinction is
    the memory claim; collapsing the two lists would make it unverifiable.
    """

    case_id: str
    prior_closed_cases: list[RetrievalHit] = Field(default_factory=list)
    sentinel_cases: list[RetrievalHit] = Field(default_factory=list)
    cited: list[str] = Field(
        default_factory=list,
        description="The ids that reached answer.case.similar_prior_cases.",
    )
    retrieval: RetrievalBreakdown = Field(default_factory=RetrievalBreakdown)


class CaseDetail(BaseModel):
    """``GET /api/cases/{case_id}`` — everything the case screen opens with.

    Responsibility: one request for the whole screen. The alert is always
    there; the answer, the run, the validation and the action plan are null
    until the investigation produces them, so the screen renders in every state
    a case can be in rather than only the finished one.
    """

    alert: Alert
    answer: AnswerFile | None = None
    run: RunSummary | None = None
    validation: ValidationBody | None = None
    trace_available: bool = False
    actions: ActionPlan | None = None


# ── the graph canvas ─────────────────────────────────────────────────────────


class GraphNode(BaseModel):
    """One vertex on the case canvas, with the attributes its shape encodes.

    ``n_cards`` is carried for device profiles specifically: a fingerprint
    shared with 842 cards is not evidence, and the canvas fades it rather than
    drawing it as strongly as a device seen on two.
    """

    id: str
    kind: GraphNodeKind
    label: str = ""
    focus: bool = Field(
        default=False,
        description="The subject of the case — the alerted card, or the flagged transaction.",
    )
    affected: bool = Field(
        default=False,
        description="A member of answer.case.affected_txn_ids; drawn with the danger ring.",
    )
    risk_score: float | None = None
    amount_usd: float | None = None
    at: datetime | None = None
    n_cards: int | None = None
    attrs: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """One edge, named by its type in ``graph/schema.gsql``."""

    id: str
    source: str
    target: str
    type: str
    label: str = ""
    affected: bool = False


class GraphLegendEntry(BaseModel):
    """One row of the always-visible legend: a kind, its wording and its count."""

    kind: GraphNodeKind
    label: str
    count: int = 0


class GraphCanvas(BaseModel):
    """``GET /api/graph/cases/{case_id}`` — the subgraph, capped and labelled.

    Responsibility: return a drawable subgraph and say plainly when it is not
    the whole one. ``shown`` and ``total`` exist so the truncation chip can
    read "showing 60 of 214 — expand" rather than the canvas silently lying
    about the size of a ring.
    """

    case_id: str
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    truncated: bool = False
    shown: int = 0
    total: int = 0
    legend: list[GraphLegendEntry] = Field(default_factory=list)


class RingComponent(BaseModel):
    """One connected component the ring sweep kept.

    A component is not yet a ring: R6 requires it to be multi-customer,
    time-concentrated and to match no documented pattern. ``is_ring`` is the
    sweep's own verdict after those gates, so the console can draw a candidate
    and a confirmed ring differently instead of implying every cluster counts.
    """

    component_id: str = ""
    cards: list[str] = Field(default_factory=list)
    devices: list[str] = Field(default_factory=list)
    seeds: list[str] = Field(default_factory=list)
    customers: list[str] = Field(default_factory=list)
    confirmed_fraud_cards: list[str] = Field(default_factory=list)
    exposure_usd: float = 0.0
    is_ring: bool = False


class RingDeviceLink(BaseModel):
    """One device profile and the cards seen on it inside the window.

    Recorded whether or not it formed a component, because a sweep that
    reports only its hits cannot be told apart from a sweep that never ran.
    """

    device: str = ""
    cards: list[str] = Field(default_factory=list)
    seeds: list[str] = Field(default_factory=list)


class RingReport(BaseModel):
    """``GET /api/graph/rings`` — the ring sweep, as the CLI last wrote it.

    Read from ``exploration/rings.json`` rather than recomputed: the sweep
    costs a few hundred graph calls and the console is not the place to spend
    them. Serving the artefact also means the page and
    ``python -m sentinel explore rings`` cannot disagree.

    ``available`` false is the honest answer when the sweep has never run —
    distinct from a sweep that ran and found nothing, which is
    ``rings_found: 0`` and is a result.
    """

    available: bool = False
    generated_at: str = ""
    hops: int = 1
    window_days: int = 30
    max_device_cards: int = 20
    seeds: int = 0
    generic_profiles_skipped: int = 0
    #: How far two hops reaches. On this pack it is a giant component, which is
    #: the measurement that rules the second hop out rather than a finding.
    two_hop_reach: int = 0
    two_hop_customers: int = 0
    components_found: int = 0
    rings_found: int = 0
    components: list[RingComponent] = Field(default_factory=list)
    #: The one fingerprint worth drawing: the profile spanning the most
    #: customers at *two* hops. It satisfies the same component test the ring
    #: gate applies — multi-customer, bounded, with confirmed fraud already on
    #: file — but at a hop R6 is not defined on, so it is reported here and
    #: never counted in ``rings_found``.
    focus: RingComponent | None = None
    #: Every device the sweep looked at. The evidence for the negative result,
    #: and what the console draws when there is no ring to draw.
    examined: list[RingDeviceLink] = Field(default_factory=list)
    note: str = ""


# ── the benchmark ────────────────────────────────────────────────────────────


class BenchmarkRequest(BaseModel):
    """``POST /api/benchmark/runs`` — run the pack.

    ``concurrency`` defaults to 1 because the cases are not independent: a
    later case may retrieve the ``FraudCase`` an earlier one wrote, and running
    them in parallel makes which memories existed a race.
    """

    case_ids: list[str] = Field(
        default_factory=list, description="Empty means every alert in the case pack."
    )
    order: BatchOrder = BatchOrder.CHRONOLOGICAL
    concurrency: int = Field(default=1, ge=1, le=8)
    mode: RunMode = RunMode.LIVE


class BenchmarkAccepted(BaseModel):
    """202 from ``POST /api/benchmark/runs`` — the batch and where to watch it."""

    batch_id: str
    stream_url: str
    case_ids: list[str] = Field(default_factory=list)


class HistogramBin(BaseModel):
    """One bucket of the probability histogram, half-open on the upper edge."""

    lower: float
    upper: float
    count: int = 0


class BenchmarkCase(BaseModel):
    """One case's outcome in a batch, as the benchmark table renders a row."""

    case_id: str
    ok: bool = False
    verdict: Verdict | None = None
    fraud_probability: float | None = None
    pattern: Pattern | None = None
    actions: list[Action] = Field(default_factory=list)
    sar: bool | None = None
    tool_calls: int = 0
    tokens: int = 0
    elapsed_s: float = 0.0
    error: str = ""
    errors: list[ValidationFinding] = Field(default_factory=list)


class BenchmarkReport(BaseModel):
    """``GET /api/benchmark/report`` — whether the last batch is defensible.

    Responsibility: the four ratios that say a run has stopped discriminating,
    and the warnings derived from them. Half the benchmark is legitimate
    activity, so a block rate over 0.50, a SAR rate over 0.20 or one verdict
    covering more than 60 % of the pack is the shape of an over-eager agent —
    the failure mode Guide.md names.
    Collaborators: ``sentinel.runner.BatchReport``, which computes the same
    ratios for the CLI; this is its HTTP form.
    """

    batch_id: str = ""
    total: int = 0
    valid: int = 0
    verdict_mix: dict[Verdict, int] = Field(default_factory=dict)
    block_rate: float = 0.0
    sar_rate: float = 0.0
    changed_rate: float = Field(
        default=0.0,
        description="Share of cases where asking for evidence actually moved the answer.",
    )
    probability_histogram: list[HistogramBin] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    elapsed_s: float = 0.0
    cases: list[BenchmarkCase] = Field(default_factory=list)


# ── problem details ──────────────────────────────────────────────────────────


class Problem(BaseModel):
    """RFC 9457 problem details — the body of every error this API returns.

    Declared here so the client has a type for it: a typed fetch wrapper that
    cannot read ``code`` off a failure has to parse error prose instead, which
    is how a demo ends up showing "Something went wrong".
    """

    type: str
    title: str
    status: int
    code: str
    detail: str
    request_id: str = ""


class ApprovalRef(BaseModel):
    """The approval a denied execute just enqueued, inside the 403 body."""

    approval_id: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    href: str = ""


class ForbiddenRouteProblem(Problem):
    """The 403 that names who can approve.

    Unusual REST, taken deliberately: a denied execute returns 403 *and*
    idempotently enqueues the approval, so the exit test is one click from
    blocked to inbox. ``on_denied: "reject"`` opts out and leaves ``approval``
    null.
    """

    action: Action
    required_route: Route
    your_role: Role
    roles_that_can_approve: list[Role] = Field(default_factory=list)
    approval: ApprovalRef | None = None


# ── query parameters ─────────────────────────────────────────────────────────
#
# Modelled rather than left as loose function arguments so that the filters are
# part of the generated contract: the console builds these query strings, and a
# renamed parameter should break its build rather than silently return
# everything.


class CaseQuery(BaseModel):
    """``GET /api/cases`` — filters over the queue."""

    status: CaseStatus | None = None
    verdict: Verdict | None = None
    trigger_type: TriggerType | None = None
    sort: CaseSort = CaseSort.OPENED_AT
    q: str = Field(default="", description="Free text over case id, card, customer and summary.")
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class TraceQuery(BaseModel):
    """``GET /api/cases/{case_id}/trace`` — which run's trace. Latest when empty."""

    run_id: str = ""


class ValidationQuery(BaseModel):
    """``GET /api/cases/{case_id}/validation``.

    ``graph`` is false by default because the graph half costs a round trip per
    batch of ids and the pure half is what blocks a write.
    """

    graph: bool = False


class ActionsQuery(BaseModel):
    """``GET /api/cases/{case_id}/actions`` — one phase, or both when null."""

    phase: Phase | None = None


class RunQuery(BaseModel):
    """``GET /api/investigations``."""

    case_id: str = ""
    status: RunStatus | None = None
    limit: int = Field(default=50, ge=1, le=200)


class StreamQuery(BaseModel):
    """``GET /api/investigations/{run_id}/events`` — the SSE query fallbacks.

    ``EventSource`` cannot send headers, so the role and the resume point have
    to be expressible in the URL. The console uses ``fetch`` and sends both as
    headers; these exist so a ``curl`` in the demo, and a browser that falls
    back, still work. HLD ADR-5.
    """

    last_event_id: int = Field(default=0, ge=0)
    speed: float = Field(
        default=1.0, gt=0.0, le=100.0, description="Replay speed multiplier. Ignored on a live run."
    )
    role: Role | None = None


class EventRangeQuery(BaseModel):
    """``GET /api/investigations/{run_id}/events.json`` — a ``seq`` range.

    ``from`` is a Python keyword, so the field is ``from_seq`` and the alias is
    what appears on the wire and in the generated contract.
    """

    model_config = ConfigDict(populate_by_name=True)

    from_seq: int = Field(default=0, ge=0, alias="from")
    to_seq: int | None = Field(default=None, ge=0, alias="to")
    limit: int = Field(default=1000, ge=1, le=5000)


class ExecutionQuery(BaseModel):
    """``GET /api/actions/executions``. ``status`` filters on the outcome."""

    case_id: str = ""
    status: ExecutionOutcome | None = None
    limit: int = Field(default=50, ge=1, le=200)


class ApprovalQuery(BaseModel):
    """``GET /api/approvals``."""

    status: ApprovalStatus | None = None
    route: Route | None = None
    case_id: str = ""


class AuditQuery(BaseModel):
    """``GET /api/audit``."""

    case_id: str = ""
    actor: str = ""
    action: str = ""
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class GraphQuery(BaseModel):
    """``GET /api/graph/cases/{case_id}``.

    ``max_nodes`` defaults low and the response says when it bound: ungated,
    the 116 device profiles carrying 24,653 links merge the graph into one
    component and the canvas becomes a hairball rather than a case.
    """

    depth: int = Field(default=2, ge=1, le=3)
    include_ring: bool = True
    max_nodes: int = Field(default=60, ge=10, le=500)


# ── paged responses ──────────────────────────────────────────────────────────
#
# Named parametrisations of :class:`Page`, so each list endpoint has one
# response model to declare and the emitter has one alias to generate.

CasePage = Page[CaseSummary]
RunPage = Page[RunSummary]
ExecutionPage = Page[ExecutionRecord]
AuditPage = Page[AuditItem]
ApprovalPage = Page[ApprovalItem]
