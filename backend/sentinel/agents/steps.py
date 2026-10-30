"""The ten steps of an investigation.

The names are a contract, not a convenience. They appear in the SSE stream, in
the UI timeline and in the persisted trace, and the counter that numbers them is
the same one that fills ``answer.evidence_requests[].asked_after_step``.

Six of the ten touch no model at all. That is the point: the model chooses what
to ask, names what it sees and writes the prose, and everything that becomes a
graded number or a policy decision is computed.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import ClassVar

from sentinel.agents.agents.assessment import AssessmentAgent, DevilsAdvocateAgent
from sentinel.agents.agents.narration import NarrationAgent
from sentinel.agents.agents.planner import PlannerAgent
from sentinel.agents.assembler import AnswerAssembler
from sentinel.agents.context import InvestigationContext
from sentinel.agents.episode import EpisodeScoper
from sentinel.agents.state_builder import CaseStateBuilder, ScopedFacts
from sentinel.domain.answer import SAR_NARRATIVE_MAX_SENTENCES, count_sentences
from sentinel.domain.enums import Pattern, RequestType
from sentinel.domain.errors import BudgetExceeded, SentinelError, VertexNotFound
from sentinel.memory.store import CaseMemoryStore, CaseWriteRequest, is_closed_case
from sentinel.policy.engine import PolicyEngine, PolicyOutcome, cited_rules
from sentinel.policy.state import CaseState
from sentinel.policy.stopping import StoppingPolicy
from sentinel.rag.retriever import (
    KIND_CLOSED_CASE,
    KIND_POLICY,
    GraphRagRetriever,
    RetrievalQuery,
)
from sentinel.simulation.simulator import EvidenceSimulator
from sentinel.tools.dto import (
    AmountBandProbe,
    CardAmountStats,
    CardCaseMemory,
    CardTestingProbe,
    CardWindow,
    CustomerCaseHistory,
    DeviceNovelty,
    ProductNovelty,
    RecurringChargeProbe,
    RegionNovelty,
    RingExpansion,
    TxnDetail,
    TxnSequenceContext,
)
from sentinel.tools.log import ToolResult

logger = logging.getLogger(__name__)


class InvestigationStep(ABC):
    """One numbered stage. Advances the context and emits what it did.

    A step never writes a file, never picks a route and never calls a model
    directly — it delegates to an ``LlmAgent`` when it needs one.
    """

    name: ClassVar[str]
    title: ClassVar[str]

    @abstractmethod
    async def run(self, ctx: InvestigationContext) -> None: ...

    def skip(self, ctx: InvestigationContext) -> bool:
        """True when this step has nothing to do for this case."""
        return False


# ── 1. scope ─────────────────────────────────────────────────────────────────


class ScopeStep(InvestigationStep):
    """Resolve the alert to its transaction, and anchor the run on its clock."""

    name = "scope"
    title = "Scope the alert"

    async def run(self, ctx: InvestigationContext) -> None:
        result = await _call(ctx, "txn_detail", txn_id=ctx.flagged_txn_id)
        if not result.ok or not isinstance(result.data, TxnDetail):
            raise VertexNotFound(
                f"flagged transaction {ctx.flagged_txn_id} could not be read: {result.error}",
                vertex_type="Transaction",
                vertex_id=ctx.flagged_txn_id,
            )
        ctx.txn = result.data.txn
        # Everything downstream anchors on the transaction's own timestamp. The
        # alert is one to six hours later on all twenty and never equal.
        ctx.post_all(ctx.extractor.from_txn_detail(result.data, result.ref))


# ── 2. plan ──────────────────────────────────────────────────────────────────


class PlanStep(InvestigationStep):
    """Choose the detectors. The one step where a model decides what happens."""

    name = "plan"
    title = "Plan the investigation"

    def __init__(self, planner: PlannerAgent) -> None:
        self._planner = planner

    async def run(self, ctx: InvestigationContext) -> None:
        """The planner orders a fixed plan and says why. See PlannerAgent.

        It neither chooses nor extends the detector set. Both were tried
        against the live model and both were measured: choosing freely, it
        dropped the five exonerating detectors and returned p = 0.74 instead of
        0.36 on identical facts; adding to a mandatory core, it added a
        different set on each of four identical runs and returned 0.3569,
        0.3577 or 0.5025.
        """
        output = await self._planner.run(ctx)
        ctx.planned = PlannerAgent.order(output, ctx)
        ctx.planner_notes = PlannerAgent.rationale(output, ctx)


# ── 3. sweep ─────────────────────────────────────────────────────────────────


class SweepStep(InvestigationStep):
    """Run the plan, normalise what comes back, post what it evidences."""

    name = "sweep"
    title = "Gather evidence from the graph"

    async def run(self, ctx: InvestigationContext) -> None:
        txn = ctx.txn
        assert txn is not None, "scope must run first"
        card = ctx.alert.card_id
        region = txn.addr1

        # Arguments are built here, from measurements, so a planner cannot shift
        # a window by passing the alert's time instead of the transaction's.
        arguments: dict[str, dict[str, object]] = {
            "card_baseline": {"card_id": card},
            "card_window": {"card_id": card, "center": ctx.as_of, "hours": 72},
            "amount_band_probe": {"card_id": card, "amt": txn.amt, "as_of": ctx.as_of},
            "card_amount_stats": {"card_id": card, "as_of": ctx.as_of},
            "region_novelty": {"card_id": card, "region": region, "as_of": ctx.as_of},
            "device_novelty": {"txn_id": txn.txn_id},
            "txn_sequence_context": {"txn_id": txn.txn_id},
            "customer_case_history": {"customer_id": ctx.alert.customer_id},
            "case_memory_for_card": {"card_id": card},
            "card_testing_probe": {"card_id": card, "center": ctx.as_of, "small_amt": 5.0},
            "velocity_probe": {"card_id": card, "center": ctx.as_of, "hours": 48},
            "recurring_charge_probe": {"card_id": card, "amt": txn.amt, "product": txn.product},
            "product_novelty": {"card_id": card, "product": txn.product, "as_of": ctx.as_of},
            "ring_expand": {
                "card_id": card,
                "center": ctx.as_of,
                "days": 30,
                # A correctness gate, never left to a planner: 116 of 9,704
                # profiles carry 24,653 of the card links.
                "max_device_cards": 20,
            },
            "device_neighbors": {
                "device_key": txn.device_key,
                "center": ctx.as_of,
                "days": 30,
            },
        }

        plan = [name for name in getattr(ctx, "planned", []) if name in arguments]
        if region == "":
            plan = [n for n in plan if n != "region_novelty"]
        if not txn.device_key:
            plan = [n for n in plan if n != "device_neighbors"]

        stats: CardAmountStats | None = None
        band: AmountBandProbe | None = None

        for name in plan:
            try:
                ctx.guard.check_tool_call()
            except BudgetExceeded:
                logger.info("sweep: tool budget reached, %d detector(s) not run", len(plan))
                break
            result = await _call(ctx, name, **arguments[name])
            if not result.ok:
                continue
            data = result.data

            if isinstance(data, CardWindow):
                ctx.window = data
            elif isinstance(data, CardTestingProbe):
                ctx.facts = _with(ctx.facts, testing=data)
            elif isinstance(data, RecurringChargeProbe):
                ctx.facts = _with(ctx.facts, recurring=data)
            elif isinstance(data, RingExpansion):
                ctx.facts = _with(ctx.facts, ring=data)
                ctx.post_all(ctx.extractor.from_ring(data, result.ref, ctx.alert.card_id))
                ctx.connected_card_ids = [c.card_id for c in data.connected_cards]
                ctx.connected_device_profiles = [
                    d.label for d in data.identifying_devices if d.label
                ]
            elif isinstance(data, CustomerCaseHistory):
                ctx.facts = _with(ctx.facts, history=data)
            elif isinstance(data, RegionNovelty):
                ctx.region_novelty = data
                ctx.post_all(
                    ctx.extractor.from_region_novelty(data, result.ref, ctx.alert.card_id, region)
                )
            elif isinstance(data, DeviceNovelty):
                ctx.device_novelty = data
                ctx.post_all(ctx.extractor.from_device_novelty(data, result.ref, txn.txn_id))
            elif isinstance(data, TxnSequenceContext):
                ctx.post_all(ctx.extractor.from_sequence(data, result.ref, txn.txn_id))
            elif isinstance(data, ProductNovelty):
                ctx.post_all(
                    ctx.extractor.from_product_novelty(
                        data, result.ref, ctx.alert.card_id, txn.product
                    )
                )
            elif isinstance(data, CardAmountStats):
                stats = data
            elif isinstance(data, AmountBandProbe):
                band = data
                ctx.amount_ref = result.ref
            elif isinstance(data, CardCaseMemory):
                ctx.similar_prior_cases = [c.case_id for c in data.bank_closed_cases][:6]

        # The amount family needs both results, so it posts once both are in.
        if band is not None:
            ctx.post_all(
                ctx.extractor.from_amount_band(
                    band, stats, ctx.amount_ref, ctx.alert.card_id, txn.amt
                )
            )


# ── 4. recall ────────────────────────────────────────────────────────────────


class RecallStep(InvestigationStep):
    """Retrieve prior cases and the policy text the assessment reasons over.

    Two retrievals, not one. Closed cases answer "has the bank seen this shape
    before, and what did the analyst do?". Policy chunks answer "which written
    rule governs it?" — and they are what make ``source: "document"`` evidence
    reachable, which is GraphRAG's only visible footprint in the graded file.

    Without a retriever the step degrades to the structural lookup it had
    before, rather than failing: a run against the fake, or against a graph
    whose corpus has not been ingested, still produces a valid answer.
    """

    name = "recall"
    title = "Recall prior cases and policy"

    def __init__(self, retriever: GraphRagRetriever | None = None) -> None:
        self._retriever = retriever

    async def run(self, ctx: InvestigationContext) -> None:
        if self._retriever is not None:
            await self._hybrid(ctx)
        if not ctx.similar_prior_cases:
            await self._structural(ctx)

    async def _hybrid(self, ctx: InvestigationContext) -> None:
        assert self._retriever is not None
        query = self._describe(ctx)
        as_of = ctx.txn.ts if ctx.txn is not None else ctx.alert.opened_at
        try:
            cases = await self._retriever.retrieve(
                RetrievalQuery(
                    text=query,
                    kind=KIND_CLOSED_CASE,
                    pattern=ctx.pattern,
                    exposure=abs(ctx.txn.amt) if ctx.txn is not None else 0.0,
                    card_id=ctx.alert.card_id,
                    customer_id=ctx.alert.customer_id,
                    as_of=as_of,
                    k=6,
                )
            )
            docs = await self._retriever.retrieve(
                RetrievalQuery(text=query, kind=KIND_POLICY, as_of=None, k=4)
            )
        except SentinelError as exc:
            # Retrieval is grounding, not evidence. Losing it degrades the
            # answer; failing the run over it would be worse.
            logger.warning("retrieval failed for %s: %s", ctx.alert.alert_id, exc.message)
            return

        ctx.similar_prior_cases = [hit.id for hit in cases]
        ctx.retrieved = [hit.as_dict() for hit in (*cases, *docs)]
        ctx.policy_docs = {hit.id: hit.as_dict() for hit in docs}
        ctx.policy_catalogue = self._retriever.policy_catalogue()
        for kind, hits in (("closed_case", cases), ("policy_doc", docs)):
            ctx.emitter.emit(
                "retrieval.completed",
                {
                    "kind": kind,
                    "strategy": "hybrid (vector + structural, RRF)",
                    "query": query,
                    "hits": [hit.as_dict() for hit in hits],
                },
                step=ctx.counter.current,
            )

    async def _structural(self, ctx: InvestigationContext) -> None:
        result = await _call(ctx, "case_memory_for_card", card_id=ctx.alert.card_id)
        if result.ok and isinstance(result.data, CardCaseMemory):
            ctx.similar_prior_cases = [c.case_id for c in result.data.bank_closed_cases][:6]
        ctx.emitter.emit(
            "retrieval.completed",
            {
                "kind": "closed_case",
                "strategy": "structural",
                "query": f"card {ctx.alert.card_id}",
                "hits": [{"id": case_id} for case_id in ctx.similar_prior_cases],
            },
            step=ctx.counter.current,
        )

    @staticmethod
    def _describe(ctx: InvestigationContext) -> str:
        """The case in the words a closed-case note would use.

        Built from the same ``CaseState`` the policy engine will decide on, not
        from a second reading of the raw DTOs and not from model prose. The
        query text decides what is retrieved: a threshold applied differently
        here than in the engine would retrieve cases about a different case,
        and a hallucinated phrase would retrieve cases about nothing.
        """
        txn = ctx.txn
        state = _state(ctx)
        parts: list[str] = [ctx.alert.trigger_text.strip()]
        if txn is not None:
            channel = "card-present" if txn.channel == "in_person" else "online"
            parts.append(
                f"{channel} transaction of ${abs(txn.amt):,.2f} on card {ctx.alert.card_id}"
                f" in billing region {txn.addr1}"
            )
        if ctx.region_novelty is not None and ctx.region_novelty.prior_txns_in_region == 0:
            parts.append("billing region is new for this card")
        if ctx.device_novelty is not None and ctx.device_novelty.device_is_new_to_card:
            parts.append("device profile is new for this account")
        if state.recurring_match:
            parts.append("the amount matches a recurring monthly charge on this card")
        if state.card_testing_sequence:
            parts.append("several small authorizations preceded a larger purchase")
        if state.shared_origin:
            parts.append(f"several cards share the same {state.shared_origin_kind}")
        if state.pattern is not Pattern.NONE:
            parts.append(f"suspected pattern {state.pattern.value}")
        return ". ".join(part for part in parts if part)


# ── 5. assess ────────────────────────────────────────────────────────────────


class AssessStep(InvestigationStep):
    """Name the pattern, then argue the other side of it."""

    name = "assess"
    title = "Assess and challenge"

    def __init__(self, assessor: AssessmentAgent, defence: DevilsAdvocateAgent) -> None:
        self._assessor = assessor
        self._defence = defence

    async def run(self, ctx: InvestigationContext) -> None:
        assessment = await self._assessor.run(ctx)
        ctx.pattern = _coherent_pattern(assessment.pattern, ctx)
        ctx.narration.summary = assessment.summary
        ctx.narration.pattern_description = (
            assessment.pattern_description if ctx.pattern is Pattern.UNDOCUMENTED else ""
        )

        exoneration = await self._defence.run(ctx)
        accepted = DevilsAdvocateAgent.to_postings(exoneration, ctx)
        ctx.post_all(accepted)
        ctx.facts = _with(ctx.facts, pattern=ctx.pattern, evidence_conflicts=bool(accepted))


# ── 6. stop_test ─────────────────────────────────────────────────────────────


class StopTestStep(InvestigationStep):
    """Policy §6: is the evidence already decisive enough to act on."""

    name = "stop_test"
    title = "Test whether to stop"

    def __init__(self, stopping: StoppingPolicy) -> None:
        self._stopping = stopping

    async def run(self, ctx: InvestigationContext) -> None:
        state = _state(ctx)
        decision = self._stopping.should_stop(state, ctx.ledger.independent_support())
        ctx.stop_before_request = decision.stop
        if decision.stop:
            ctx.narration.stop_reason = decision.reason


# ── 7. request_evidence ──────────────────────────────────────────────────────


class RequestEvidenceStep(InvestigationStep):
    """Ask for what is missing, and answer it from the graph, with the basis stated."""

    name = "request_evidence"
    title = "Request further evidence"

    def skip(self, ctx: InvestigationContext) -> bool:
        return ctx.stop_before_request

    async def run(self, ctx: InvestigationContext) -> None:
        try:
            ctx.guard.check_evidence_round()
        except BudgetExceeded:
            return
        ctx.guard.record_evidence_round()
        # Frozen before anything this step posts, because `initial` means
        # "before I asked" and the ledger is about to move.
        ctx.pre_request = ctx.ledger.snapshot()

        # Which question is worth asking, and of whom.
        #
        # A step-up challenge only means something when there is a device to
        # challenge; seven of the twenty alerts carry no identity record at
        # all. And on a `customer_report` alert the cardholder has already
        # answered "did you make this" — asking them to validate a charge they
        # just reported is not a real action, so the step-up is the only
        # question left for them.
        if _step_up_is_answerable(ctx) and (ctx.already_denied or ctx.ledger.p >= 0.5):
            request_type = RequestType.STEP_UP_AUTH
        elif ctx.already_denied:
            # Policy 5's third option. The cardholder has already answered the
            # only question they can answer and there is no device to
            # challenge, so the party with something left to add is an analyst.
            request_type = RequestType.ANALYST_INFO
        else:
            request_type = RequestType.CUSTOMER_VALIDATION
        simulator = EvidenceSimulator(
            recurring=ctx.facts.recurring,
            region=ctx.region_novelty,
            history=ctx.facts.history,
            device=ctx.device_novelty,
            refs=dict(ctx.refs),
            already_denied=ctx.already_denied,
        )
        response = simulator.simulate(request_type)
        ctx.response = response
        # R2, R3 and R4 turn on *what the cardholder said*. An analyst reading
        # the same history and concluding it looks like theirs is real evidence
        # — it posts to the ledger below — but it is not the cardholder
        # speaking, and routing it through `customer_response` produced a case
        # file whose recommendation read "R3: cardholder confirms the
        # transaction" beside an `evidence_requests` entry that said an analyst
        # had reviewed it. HHG-009 came out recommending CREATE_CASE and
        # CLOSE_NO_FRAUD together on the strength of it.
        if request_type is not RequestType.ANALYST_INFO:
            ctx.customer_response = response.branch
        ctx.evidence_request_step = ctx.counter.current
        ctx.narration.assumed_response = response.assumed_response

        if response.log_lr != 0.0:
            posting = ctx.ledger.post_judgement(
                group="response",
                log_lr=response.log_lr,
                claim=response.claim,
                ref="evidence_request:1",
            )
            ctx.emitter.emit(
                "evidence.posted",
                {
                    "feature": posting.feature,
                    "present": True,
                    "group": posting.group,
                    "claim": posting.claim,
                    "source": posting.source.value,
                    "ref": posting.ref,
                    "entity_ids": [],
                    "lr": round(posting.lr, 4),
                    "log_lr": round(posting.log_lr, 4),
                    "p_before": round(posting.p_before, 4),
                    "p_after": round(posting.p_after, 4),
                    "capped": posting.capped,
                },
                step=ctx.counter.current,
            )

        ctx.emitter.emit(
            "evidence.requested",
            {
                "request_index": 1,
                "type": request_type.value,
                "rationale": "The evidence was not decisive under policy section 6.",
                "assumed_response": response.assumed_response,
                "assumption_basis": list(response.assumption_basis),
                "branch": response.branch.value,
                "log_lr_applied": response.log_lr,
            },
            step=ctx.evidence_request_step,
        )


# ── 8. decide ────────────────────────────────────────────────────────────────


class DecideStep(InvestigationStep):
    """Two policy evaluations: before the request, and after it."""

    name = "decide"
    title = "Decide under policy"

    def __init__(self, engine: PolicyEngine) -> None:
        self._engine = engine

    async def run(self, ctx: InvestigationContext) -> None:
        txn = ctx.txn
        assert txn is not None

        ctx.verdict = CaseStateBuilder(ctx.ledger).verdict()
        scoper = EpisodeScoper(
            window=ctx.window, testing=ctx.facts.testing, region=ctx.region_novelty
        )
        ctx.episode = scoper.scope(txn, ctx.pattern, ctx.verdict, txn.addr1)

        # `initial` is the recommendation BEFORE any requested evidence came
        # back — but *including* whatever the trigger itself already said, so
        # the eight cases where the cardholder opened with "I never made this
        # purchase" reach R2 in their initial recommendation, not only their
        # final one. Evaluating it after the request would make the pair
        # incomparable; evaluating it as if the trigger had said nothing would
        # make it wrong.
        before = _state(ctx, _INITIAL)
        initial = self._engine.evaluate(before)
        ctx.initial = initial.recommendations
        ctx.emitter.emit(
            "policy.evaluated",
            _policy_payload(ctx, before, initial, "initial"),
            step=ctx.counter.current,
        )

        if ctx.response is None:
            # Nothing was asked, so final must deep-equal initial. The validator
            # makes any difference a hard error.
            ctx.final = list(ctx.initial)
            ctx.sar_file = initial.sar.file
            ctx.sar_reason = initial.sar.reason
            ctx.gates_fired = [gate.as_dict() for gate in initial.gates if gate.fired]
            _record_rules(ctx)
            return

        after = _state(ctx, _CURRENT)
        final = self._engine.evaluate(after)
        ctx.final = final.recommendations
        ctx.sar_file = final.sar.file
        ctx.sar_reason = final.sar.reason
        ctx.gates_fired = [gate.as_dict() for gate in final.gates if gate.fired]
        ctx.emitter.emit(
            "policy.evaluated",
            _policy_payload(ctx, after, final, "final"),
            step=ctx.counter.current,
        )
        _record_rules(ctx)


# ── 9. narrate ───────────────────────────────────────────────────────────────


class NarrateStep(InvestigationStep):
    """Write the prose, once every number is settled."""

    name = "narrate"
    title = "Write the case"

    def __init__(self, narrator: NarrationAgent) -> None:
        self._narrator = narrator

    async def run(self, ctx: InvestigationContext) -> None:
        output = await self._narrator.run(ctx)
        ctx.narration.sar_narrative = _fit_narrative(output.sar_narrative) if ctx.sar_file else ""
        ctx.narration.what_changed = output.what_changed or "nothing"
        if not ctx.narration.stop_reason:
            ctx.narration.stop_reason = output.stop_reason


# ── 10. write ────────────────────────────────────────────────────────────────


class WriteStep(InvestigationStep):
    """Put the case in the graph, so the next case can find it.

    The step writes a *vertex*, never a file — that is the run harness's job, and
    keeping it so is what lets the API run an investigation without touching the
    submission directory. What it does own is the claim: after this step,
    ``ctx.written_to_graph`` and ``ctx.graph_case_id`` are true and set, and the
    assembler copies them into the answer. Nothing else in the system may set
    them, so an answer claiming a write-back is an answer that got one.

    It assembles a provisional answer to hand the store, because a `FraudCase`
    vertex carries the verdict, the exposure and the summary and those come off
    the assembled object rather than being re-derived here. The two fields this
    step sets are the only difference between that provisional answer and the
    one the orchestrator assembles a moment later.
    """

    name = "write"
    title = "Write the case to the graph"

    def __init__(self, store: CaseMemoryStore, assembler: AnswerAssembler | None = None) -> None:
        self._store = store
        self._assembler = assembler or AnswerAssembler()

    async def run(self, ctx: InvestigationContext) -> None:
        provisional = self._assembler.assemble(ctx)
        cited = list(ctx.similar_prior_cases)
        request = CaseWriteRequest(
            case_id=ctx.alert.alert_id,
            answer=provisional,
            alert_id=ctx.alert.alert_id,
            customer_id=ctx.alert.customer_id,
            card_id=ctx.alert.card_id,
            device_keys=list(ctx.connected_device_profiles),
            # `similar_prior_cases` holds both kinds once the retriever runs:
            # the bank's `CC-NNNN` rows and Sentinel cases written earlier in
            # this same batch. They take different edge types.
            cites_closed_cases=[c for c in cited if is_closed_case(c)],
            cites_cases=[c for c in cited if not is_closed_case(c)],
            applied_rules=list(ctx.applied_rules),
            created_at=ctx.alert.opened_at,
        )
        try:
            result = await self._store.write(request)
        except SentinelError as exc:
            # A failed write-back must not lose the investigation. The answer is
            # still valid; it simply cannot claim a vertex that is not there.
            logger.warning("write-back failed for %s: %s", ctx.alert.alert_id, exc.message)
            ctx.emitter.emit(
                "case.write_failed",
                {"case_id": ctx.alert.alert_id, "code": exc.code, "message": exc.message},
                step=ctx.counter.current,
            )
            return

        ctx.written_to_graph = result.ok
        ctx.graph_case_id = result.graph_case_id if result.ok else ""
        ctx.emitter.emit("case.written", result.as_dict(), step=ctx.counter.current)


# ── helpers ──────────────────────────────────────────────────────────────────


async def _call(ctx: InvestigationContext, name: str, **args: object) -> ToolResult:
    """Dispatch one tool, count it against the budget, record its citation."""
    ctx.guard.record_tool_call()
    result = await ctx.tools.call(name, args)
    ctx.remember_ref(name, result.ref)
    ctx.emitter.emit(
        "tool.called",
        {
            "tool": name,
            "ref": result.ref,
            "params": {k: str(v) for k, v in args.items()},
            "entity_ids": list(result.entity_ids),
            "elapsed_s": round(result.elapsed_s, 3),
            "ok": result.ok,
            "error": result.error,
        },
        step=ctx.counter.current,
    )
    return result


#: Which phase a state is being built for. Not a boolean, because
#: ``CustomerResponse.DENIED`` is the same enum member whether the cardholder
#: volunteered it or answered a question — so the two phases cannot be told
#: apart by comparing the responses, only by saying which one is being built.
_INITIAL = "initial"
_CURRENT = "current"


def _state(ctx: InvestigationContext, phase: str = _CURRENT) -> CaseState:
    """The policy's input at one moment.

    ``_CURRENT`` is everything known now. ``_INITIAL`` is what was known
    *before the agent asked for anything* — which on a ``customer_report``
    alert still includes the cardholder's own denial, because that arrived
    with the trigger rather than in answer to a question.
    """
    snapshot = None
    if phase == _INITIAL:
        response, requested = ctx.trigger_response, False
        snapshot = ctx.pre_request
    else:
        response = ctx.effective_response
        requested = ctx.customer_response is not None
    return CaseStateBuilder(ctx.ledger).build(
        facts=ctx.facts,
        trigger_type=ctx.alert.trigger_type,
        exposure_usd=ctx.episode.exposure_usd if ctx.episode else 0.0,
        connected_card_ids=ctx.connected_card_ids,
        customer_response=response,
        response_requested=requested,
        snapshot=snapshot,
    )


def _policy_payload(
    ctx: InvestigationContext, state: CaseState, outcome: PolicyOutcome, phase: str
) -> dict[str, object]:
    return {
        "phase": phase,
        # The state's probability, not the ledger's. On the `initial` phase they
        # differ: the state was built from the snapshot taken before the
        # requested evidence landed, and the timeline must show the number the
        # recommendation was actually made at.
        "fraud_probability": state.fraud_probability,
        "exposure_usd": state.exposure_usd,
        "verdict": state.verdict.value,
        "independent_support": state.independent_signals,
        "recommendations": [r.as_dict() for r in outcome.recommendations],
        "gates_applied": [gate.as_dict() for gate in outcome.gates if gate.fired],
        "sar": {"file": outcome.sar.file, "reason": outcome.sar.reason},
        "stop": {"should_stop": ctx.stop_before_request, "reason": ctx.narration.stop_reason},
    }


def _fit_narrative(narrative: str) -> str:
    """Trim a SAR narrative into the six-to-twelve band Guide.md requires.

    Nothing else in the system lets free-form prose fail a file, and this is
    the one field where it could: ``SarReport`` rejects a narrative outside the
    band, that rejection is a ``pydantic.ValidationError`` rather than a
    ``SentinelError``, and it is raised in the assembler *after* the
    orchestrator's except clauses — so one sentence too many produced no
    ``cases/<id>.json`` at all, and zero for every part of the case rather than
    just the SAR.

    Over-long is trimmed at a sentence boundary, which loses the least
    important sentences because a FinCEN narrative puts the facts first.
    Too-short is left alone: padding a regulatory filing with invented sentences
    is the one repair worse than the failure, so the answer is quarantined with
    a finding a human can read.
    """
    text = narrative.strip()
    if not text or count_sentences(text) <= SAR_NARRATIVE_MAX_SENTENCES:
        return text
    kept: list[str] = []
    for sentence in _SENTENCES.findall(text):
        kept.append(sentence.strip())
        if count_sentences(" ".join(kept)) >= SAR_NARRATIVE_MAX_SENTENCES:
            break
    trimmed = " ".join(part for part in kept if part)
    logger.warning(
        "SAR narrative ran to %d sentences; trimmed to %d",
        count_sentences(text),
        count_sentences(trimmed),
    )
    return trimmed


#: Greedy split on the same boundary :func:`count_sentences` counts, so trimming
#: and counting can never disagree about where a sentence ends.
_SENTENCES = re.compile(r"[^.!?]*[.!?]+(?=\s+[\"\u2018\u201c(\[]?[A-Z0-9]|\s*$)|[^.!?]+$")


#: What each documented pattern *requires* of the flagged transaction, taken
#: from Guide.md's own definitions. A pattern is a claim about the facts, and a
#: claim the facts contradict is not a judgement call.
def _coherent_pattern(named: Pattern, ctx: InvestigationContext) -> Pattern:
    """Reject a named pattern the measurements rule out.

    The model names the pattern, which is the right division of labour — it is
    reading typology prose against a case. But it named `out_of_region_use` on
    a card with 42 prior transactions in that very region, and the pattern is
    graded and feeds the episode scoper. Each test below is a direct reading of
    Guide.md's definition, and each rests on a number the sweep measured:

    - *Card testing* — "three or more tiny online authorizations, often under
      $5, then a larger purchase. **Confirmed by the sequence itself.**"
    - *Card-not-present fraud* — "The number is used **online** without the card."
    - *Out-of-region use* — "**Card-present** purchases in a billing region the
      cardholder **has no history in**."

    `account_takeover` and `undocumented` are not tested: neither has a
    falsifiable premise in the measurements, and inventing one would be this
    function doing the model's job rather than checking it.
    """
    txn = ctx.txn
    if txn is None or named in (Pattern.NONE, Pattern.UNDOCUMENTED, Pattern.ACCOUNT_TAKEOVER):
        return named

    online = txn.channel == "online"
    contradiction = ""
    if named is Pattern.CARD_TESTING and not _state(ctx).card_testing_sequence:
        contradiction = "no sequence of small authorisations precedes the flagged transaction"
    elif named in (Pattern.CARD_NOT_PRESENT_FRAUD, Pattern.CARD_NOT_PRESENT_NEW_DEVICE):
        if not online:
            contradiction = "the transaction was card-present"
    elif named is Pattern.OUT_OF_REGION_USE:
        region = ctx.region_novelty
        if online:
            contradiction = "the transaction was online, not card-present"
        elif region is not None and region.prior_txns_in_region > 0:
            contradiction = (
                f"the card has {region.prior_txns_in_region} prior transactions in "
                f"billing region {txn.addr1}"
            )

    if not contradiction:
        return named
    logger.info("%s: pattern '%s' rejected — %s", ctx.alert.alert_id, named.value, contradiction)
    ctx.emitter.emit(
        "pattern.rejected",
        {"named": named.value, "contradiction": contradiction, "accepted": Pattern.NONE.value},
        step=ctx.counter.current,
    )
    return Pattern.NONE


def _step_up_is_answerable(ctx: InvestigationContext) -> bool:
    """Whether there is a device to put a one-time passcode in front of."""
    return ctx.device_novelty is not None and ctx.device_novelty.observable


def _record_rules(ctx: InvestigationContext) -> None:
    """The `PolicyDoc` ids for the rules that actually produced this answer.

    Both phases, because `initial` is graded too, plus §3a whenever a filing
    decision was reached — that section is the one that distinguishes a case
    from a report, and it is the passage a reviewer of that decision needs.
    """
    rules = cited_rules([*ctx.initial, *ctx.final])
    docs = [f"POL-{rule}" for rule in rules]
    if ctx.sar_reason:
        docs.append("POL-3A")
    ctx.applied_rules = list(dict.fromkeys(docs))


def _with(facts: ScopedFacts, **changes: object) -> ScopedFacts:
    """ScopedFacts is frozen, so accumulate by replacement."""
    from dataclasses import replace

    return replace(facts, **changes)  # type: ignore[arg-type]
