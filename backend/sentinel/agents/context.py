"""What one investigation carries from step to step.

The step counter deserves a note. ``answer.evidence_requests[].asked_after_step``
is a graded field, and the SSE stream numbers its steps for the UI timeline.
Those are the same number, from the same counter, on purpose — two counters
would eventually disagree and the submission would say the request came after a
step the stream never showed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sentinel.agents.budget import BudgetGuard, RunBudget
from sentinel.agents.emitter import EventEmitter, NullEmitter
from sentinel.agents.episode import EpisodeScope
from sentinel.agents.state_builder import ScopedFacts
from sentinel.domain.alert import Alert, TriggerContext
from sentinel.domain.enums import CustomerResponse, Pattern, Verdict
from sentinel.evidence.extractor import FeatureExtractor, PostingRequest
from sentinel.evidence.ledger import EvidenceLedger, LedgerSnapshot
from sentinel.simulation.simulator import SimulatedResponse
from sentinel.tools.dto import CardWindow, DeviceNovelty, RegionNovelty, TransactionRow
from sentinel.tools.registry import ToolRegistry


@dataclass
class StepCounter:
    """One monotonic counter per investigation, shared by the stream and the file."""

    _n: int = 0

    def next(self) -> int:
        self._n += 1
        return self._n

    @property
    def current(self) -> int:
        return self._n


@dataclass
class Narration:
    """The seven strings the model is allowed to write, and nothing else.

    Every other field of the answer file is computed. Keeping the prose in one
    object is what makes that boundary checkable rather than aspirational: the
    assembler takes typed values from the policy engine and the ledger, and
    takes ``str`` from here.
    """

    summary: str = ""
    pattern_description: str = ""
    sar_narrative: str = ""
    sar_reason_prose: str = ""
    what_changed: str = ""
    stop_reason: str = ""
    assumed_response: str = ""


@dataclass
class InvestigationContext:
    """Everything one investigation knows, accumulated in step order.

    Responsibility: carry state between steps and expose it to the assembler. It
    holds no behaviour beyond small conveniences — a context that decided things
    would put those decisions outside every unit test.
    Collaborators: every ``InvestigationStep`` reads and writes it; the
    ``AnswerAssembler`` reads it once at the end.
    """

    alert: Alert
    tools: ToolRegistry
    ledger: EvidenceLedger
    extractor: FeatureExtractor
    guard: BudgetGuard
    emitter: EventEmitter = field(default_factory=NullEmitter)
    counter: StepCounter = field(default_factory=StepCounter)

    # ── filled by `scope` ────────────────────────────────────────────────────
    #: The flagged transaction. Every window in the run is anchored on its `ts`,
    #: never on `alert.opened_at`, which lags it by one to six hours on all 20.
    txn: TransactionRow | None = None
    facts: ScopedFacts = field(default_factory=ScopedFacts)
    #: Tool names the sweep will run: the mandatory core plus whatever the
    #: planner added to it.
    planned: list[str] = field(default_factory=list)
    #: The planner's stated reason per detector, and what became of each
    #: request: `prioritised`, `not_in_plan` or `unknown`. The detector set is
    #: fixed, so this is where the planner's contribution is visible.
    planner_notes: list[dict[str, str]] = field(default_factory=list)
    #: Results the later steps need in their own right, rather than only as
    #: postings: the episode scoper reads the window, the simulator reads the
    #: region and device facts.
    window: CardWindow | None = None
    region_novelty: RegionNovelty | None = None
    device_novelty: DeviceNovelty | None = None
    #: The amount family posts once both of its two queries have returned.
    amount_ref: str = ""
    #: Citation per tool name, so the simulator can name what a branch rests on.
    refs: dict[str, str] = field(default_factory=dict)

    # ── filled by `recall` ───────────────────────────────────────────────────
    similar_prior_cases: list[str] = field(default_factory=list)
    retrieved: list[dict[str, Any]] = field(default_factory=list)
    #: The `PolicyDoc` chunks retrieval actually surfaced, keyed by doc_id.
    #: This is grounding: the assessment and narration agents read the text.
    policy_docs: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Every policy chunk in the corpus, keyed by doc_id — title and ref only.
    #: A citation must be accurate whether or not retrieval happened to rank
    #: that rule, and `decide` names the rules long after `recall` has run.
    policy_catalogue: dict[str, dict[str, str]] = field(default_factory=dict)

    # ── filled by `assess` ───────────────────────────────────────────────────
    pattern: Pattern = Pattern.NONE
    verdict: Verdict = Verdict.UNCERTAIN
    connected_card_ids: list[str] = field(default_factory=list)
    connected_device_profiles: list[str] = field(default_factory=list)

    # ── filled by `stop_test` and `request_evidence` ─────────────────────────
    stop_before_request: bool = False
    #: The ledger as it stood before any requested evidence was posted. This is
    #: what `next_best_actions.initial` is evaluated against; without it the
    #: pre-request recommendation is scored at the post-request probability.
    pre_request: LedgerSnapshot | None = None
    #: The reply to a round the agent *asked for*. Distinct from
    #: :attr:`trigger_response`, which the alert brought with it.
    response: SimulatedResponse | None = None
    customer_response: CustomerResponse | None = None
    evidence_request_step: int | None = None

    # ── filled by `decide` ───────────────────────────────────────────────────
    episode: EpisodeScope | None = None
    initial: list[Any] = field(default_factory=list)
    final: list[Any] = field(default_factory=list)
    sar_file: bool = False
    sar_reason: str = ""
    gates_fired: list[dict[str, Any]] = field(default_factory=list)

    # ── filled by `narrate` ──────────────────────────────────────────────────
    narration: Narration = field(default_factory=Narration)

    # ── filled by `write` ────────────────────────────────────────────────────
    #: Set by the write-back, never claimed by the assembler. `written_to_graph`
    #: without `graph_case_id` is rejected by the answer model, so the two are
    #: written together or not at all.
    written_to_graph: bool = False
    graph_case_id: str = ""
    #: `PolicyDoc` ids the recall step retrieved and the engine actually cited.
    #: They become `APPLIED_RULE` edges and `source: "document"` evidence.
    applied_rules: list[str] = field(default_factory=list)

    @property
    def budget(self) -> RunBudget:
        return self.guard.budget

    @property
    def flagged_txn_id(self) -> str:
        return self.alert.flagged_txn_id

    @property
    def as_of(self) -> str:
        """The anchor for every time window in this investigation.

        The transaction's own timestamp. ``Alert.opened_at`` is later by one to
        six hours on all twenty benchmark alerts and never equal, so using it
        would silently shift every window in the run.
        """
        if self.txn is None or self.txn.ts is None:
            return self.alert.opened_at.strftime("%Y-%m-%d %H:%M:%S")
        return self.txn.ts.strftime("%Y-%m-%d %H:%M:%S")

    @property
    def trigger_response(self) -> CustomerResponse | None:
        """The cardholder's position as the alert already states it.

        Eight of the twenty alerts read "Customer C08623 message: 'I never made
        this $49.00 purchase.'" — a denial, in hand at step zero, which R2 acts
        on. Until this was wired, ``TriggerContext.customer_already_denied``
        was computed and read by nothing, and R2 could not fire on the eight
        cases where the cardholder had told us outright.
        """
        return (
            CustomerResponse.DENIED
            if TriggerContext.from_alert(self.alert).customer_already_denied
            else None
        )

    @property
    def already_denied(self) -> bool:
        """Whether the alert itself is the cardholder disputing the charge."""
        return self.trigger_response is CustomerResponse.DENIED

    @property
    def effective_response(self) -> CustomerResponse | None:
        """What the cardholder has said, by whatever route."""
        return self.customer_response or self.trigger_response

    @property
    def risk_score(self) -> float:
        """The model's score, from the transaction.

        ``Alert.risk_score`` is the missing-numeric sentinel on nine of the
        twenty and is parsed to ``None``; the score that means anything is on
        the transaction.
        """
        return self.txn.risk_score if self.txn is not None else 0.0

    def post(self, request: PostingRequest) -> None:
        """Post one extracted feature and emit it, so the UI sees it move."""
        posting = self.ledger.post(**request.as_kwargs())  # type: ignore[arg-type]
        self.emitter.emit(
            "evidence.posted",
            {
                "feature": posting.feature,
                "present": posting.present,
                "group": posting.group,
                "claim": posting.claim,
                "source": posting.source.value,
                "ref": posting.ref,
                "entity_ids": list(posting.entity_ids),
                "lr": round(posting.lr, 4),
                "log_lr": round(posting.log_lr, 4),
                "p_before": round(posting.p_before, 4),
                "p_after": round(posting.p_after, 4),
                "capped": posting.capped,
            },
            step=self.counter.current,
        )

    def post_all(self, requests: list[PostingRequest]) -> None:
        for request in requests:
            self.post(request)

    def remember_ref(self, tool: str, ref: str) -> None:
        self.refs[tool] = ref
