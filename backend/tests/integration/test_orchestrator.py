"""One investigation, end to end, with no network and no model.

The graph is a fake seeded with the real HHG-003 numbers; the model is a stub.
That combination is the point: it exercises every seam between the steps — the
step counter, the budget, the ledger, the episode scoper, the policy engine, the
assembler and the validator — while staying runnable in CI with no credentials.

The case is the one with ground truth. A cardholder disputes a $49.00
card-present charge that their own card has been paying for six months, and the
right answer is not to block them.
"""

from __future__ import annotations

from typing import Any

import pytest

from sentinel.agents.agents.assessment import AssessmentOutput, ExonerationOutput
from sentinel.agents.agents.narration import NarrationOutput
from sentinel.agents.agents.planner import PlanOutput
from sentinel.agents.emitter import EVENT_TYPES, CollectingEmitter
from sentinel.agents.orchestrator import SentinelOrchestrator
from sentinel.config.settings import Settings
from sentinel.domain.alert import Alert
from sentinel.domain.enums import Action, EvidenceSource, Pattern, Route, TriggerType
from sentinel.evidence.table import DEFAULT_ELT_PATH, EvidenceLikelihoodTable
from sentinel.graph.fake import FakeGraphRepository
from sentinel.llm.client import LlmClient

CARD = "C08623-K2"
TXN = "3530164"
TS = "2016-12-10 13:01:21"
REGION = "330.0"


# ── the stub model ───────────────────────────────────────────────────────────


class StubLlm(LlmClient):
    """Returns a scripted value per purpose, and counts what was asked."""

    def __init__(self, settings: Settings, replies: dict[str, Any]) -> None:
        super().__init__(settings=settings, client=object())  # type: ignore[arg-type]
        self.replies = replies
        self.purposes: list[str] = []

    async def complete(
        self, *, purpose: str, system: str, user: str, schema: Any, max_output_tokens: int = 1200
    ):  # type: ignore[override]
        from sentinel.llm.client import LlmResponse

        self.purposes.append(purpose)
        self.prompts = getattr(self, "prompts", {})
        self.prompts[purpose] = user
        self.meter.charge(self.model, 200, 60)
        return LlmResponse(
            value=self.replies[purpose],
            model=self.model,
            purpose=purpose,
            prompt_tokens=200,
            completion_tokens=60,
            usd=0.0001,
            elapsed_s=0.01,
        )


def settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        tg_host="https://example.invalid",
        tg_secret="secret",
        openai_api_key="test",
        openai_model="gpt-5.4-mini",
        openai_embedding_model="text-embedding-3-large",
    )


# ── the graph, seeded with the real numbers ──────────────────────────────────


def seeded_graph() -> FakeGraphRepository:
    repo = FakeGraphRepository()
    repo.seed_query(
        "txn_detail",
        {
            "txn": [
                {
                    "txn_id": TXN,
                    "card_id": CARD,
                    "customer_id": "C08623",
                    "ts": TS,
                    "amt": 49.0,
                    "product": "W",
                    "channel": "in_person",
                    "risk_score": 0.4,
                    "addr1": REGION,
                    "addr2": "87.0",
                    "dist1": 9,
                    "device_key": "",
                    "device_new": "",
                    "proxy_flag": "",
                    "m_feats": "T|T|T|M0|F|T|F|F|T",
                }
            ],
            "device": [],
            "card": [{"card_id": CARD, "n_txns": 1134, "median_amt": 68.01}],
        },
    )
    repo.seed_query(
        "card_baseline",
        {
            "card": [
                {
                    "card_id": CARD,
                    "n_txns": 1134,
                    "n_in_person": 1080,
                    "median_amt": 68.01,
                    "p95_amt": 425.66,
                }
            ],
            "customer": [{"customer_id": "C08623", "n_cards": 2}],
            "sibling_cards": [
                {"SIB.card_id": CARD, "SIB.n_txns": 1134},
                {"SIB.card_id": "C08623-K1", "SIB.n_txns": 210},
            ],
        },
    )
    repo.seed_query(
        "card_window",
        {
            "window": [
                {
                    "T.txn_id": TXN,
                    "T.ts": TS,
                    "T.amt": 49.0,
                    "T.channel": "in_person",
                    "T.risk_score": 0.4,
                    "T.addr1": REGION,
                },
                {
                    "T.txn_id": "3530056",
                    "T.ts": "2016-12-10 12:12:00",
                    "T.amt": 116.93,
                    "T.channel": "in_person",
                    "T.risk_score": 0.88,
                    "T.addr1": REGION,
                },
            ]
        },
    )
    repo.seed_query(
        "region_novelty",
        {
            "prior_txns_in_region": 42,
            "total_txns_in_region": 51,
            "prior_txns_on_card": 980,
            "first_seen_in_region": "2016-07-09 23:53:35",
            "last_in_region_before_alert": "2016-12-09 10:00:00",
            "in_person_elsewhere_24h": 10,
            "other_regions_24h": ["204.0"],
        },
    )
    repo.seed_query(
        "amount_band_probe",
        {
            "prior_charges_in_band": 53,
            "total_charges_in_band": 57,
            "prior_txns_on_card": 980,
            "card_amounts": [{"C.median_amt": 68.01}],
        },
    )
    repo.seed_query(
        "card_amount_stats",
        {
            "n_prior": 980,
            "sum_amt": 126_261.64,
            "sum_amt_sq": 41_956_463.08,
            "max_prior_amt": 2680.95,
            "min_prior_amt": 4.96,
        },
    )
    repo.seed_query(
        "device_novelty",
        {
            "flags": [
                {"R.txn_id": TXN, "R.device_key": "", "R.device_new": "", "R.proxy_flag": ""}
            ],
            "prior_txns_this_device_on_card": 0,
            "prior_online_txns_on_card": 54,
            "prior_txns_on_card": 980,
        },
    )
    repo.seed_query(
        "txn_sequence_context",
        {
            "has_prev": 1,
            "has_next": 1,
            "seconds_since_prev": 2920,
            "seconds_to_next": 18661,
            "prev_txn": [],
            "next_txn": [],
        },
    )
    repo.seed_query(
        "customer_case_history",
        {
            "cases": [
                {"R.case_id": f"CC-{i:04d}", "R.card_id": CARD, "R.outcome": "confirmed_fraud"}
                for i in range(1, 6)
            ],
            "confirmed_fraud_cases": 5,
            "cleared_cases": 1,
            "customer_reports_confirmed_fraud": 5,
            "lifetime_exposure": 1154.18,
        },
    )
    repo.seed_query(
        "case_memory_for_card",
        {
            "bank_closed_cases": [{"CC.case_id": f"CC-{i:04d}"} for i in (141, 2671, 3310)],
            "sentinel_cases": [],
            "cases_connecting_this_card": [],
        },
    )
    repo.seed_query(
        "recurring_charge_probe",
        {
            "matching_charges": 57,
            "distinct_months": 6,
            "months": ["2016-7", "2016-8", "2016-9", "2016-10", "2016-11", "2016-12"],
            "regions": [REGION],
            "txn_ids": [TXN],
        },
    )
    repo.seed_query(
        "card_testing_probe",
        {
            "small_online_auths_1h": 0,
            "small_online_auths_24h": 0,
            "small_txn_ids": [],
            "largest_purchase_after": None,
            "purchases_after_ids": [],
        },
    )
    repo.seed_query("product_novelty", {"prior_txns_this_product": 900, "prior_txns_on_card": 980})
    repo.seed_query(
        "velocity_probe",
        {
            "txns_in_window": 43,
            "online_in_window": 2,
            "total_amount": 1200.0,
            "max_risk_in_window": 0.88,
            "distinct_regions": 2,
            "distinct_devices": 0,
            "regions": [REGION],
        },
    )
    return repo


ALERT = Alert(
    alert_id="HHG-003",
    opened_at="2016-12-10 15:01:21",  # type: ignore[arg-type]
    trigger_type=TriggerType.CUSTOMER_REPORT,
    trigger_text="Customer C08623 message: 'I never made this $49.00 purchase.'",
    flagged_txn_id=TXN,
    card_id=CARD,
    customer_id="C08623",
    risk_score=None,
    status="new",
)


def orchestrator(**replies: Any) -> tuple[SentinelOrchestrator, StubLlm, FakeGraphRepository]:
    defaults: dict[str, Any] = {
        "plan": PlanOutput(tools=[]),  # empty, so the static plan is used
        "claims": AssessmentOutput(
            pattern=Pattern.NONE,
            pattern_description="",
            summary=(
                "The charge matches a six-month recurring pattern on this card. "
                "The billing region and amount are both long established, so the "
                "dispute is more likely a forgotten subscription than fraud."
            ),
        ),
        "exonerate": ExonerationOutput(exonerating=[], rebuttal="The amount is routine."),
        "sar_narrative": NarrationOutput(
            sar_narrative="", what_changed="nothing", stop_reason="Evidence was settled."
        ),
    }
    defaults.update(replies)
    config = settings()
    repo = seeded_graph()
    llm = StubLlm(config, defaults)
    return (
        SentinelOrchestrator(
            settings=config,
            repository=repo,
            llm=llm,
            table=EvidenceLikelihoodTable(DEFAULT_ELT_PATH),
        ),
        llm,
        repo,
    )


# ═══ the run ═════════════════════════════════════════════════════════════════


async def test_a_full_investigation_produces_a_valid_answer():
    orch, _, _ = orchestrator()
    emitter = CollectingEmitter()
    result = await orch.run(ALERT, emitter)

    assert result.ok, [f.message for f in result.validation.errors]
    assert result.answer.case_id == "HHG-003"


async def test_the_ten_steps_run_in_order():
    orch, _, _ = orchestrator()
    emitter = CollectingEmitter()
    await orch.run(ALERT, emitter)

    started = [event.payload["name"] for event in emitter.of_type("step.started")]
    assert started == [
        "scope",
        "plan",
        "sweep",
        "recall",
        "assess",
        "stop_test",
        "request_evidence",
        "decide",
        "narrate",
    ]
    # The counter is monotonic from 1, because asked_after_step depends on it.
    numbers = [event.payload["step"] for event in emitter.of_type("step.started")]
    assert numbers == list(range(1, len(numbers) + 1))


async def test_the_emission_order_the_frontend_codes_against():
    orch, _, _ = orchestrator()
    emitter = CollectingEmitter()
    await orch.run(ALERT, emitter)
    types = emitter.types()

    assert types[0] == "run.started"
    assert types[-1] == "run.completed"
    assert types.index("verdict.reached") < types.index("validation.completed")
    assert types.index("validation.completed") < types.index("run.completed")
    # Every event emitted is one the contract declares.
    assert set(types) <= set(EVENT_TYPES)


async def test_every_window_is_anchored_on_the_transaction_not_the_alert():
    """The alert is two hours later here, and six on some cases. Using it would
    shift every window in the run and change what the evidence says."""
    orch, _, repo = orchestrator()
    await orch.run(ALERT)

    region_calls = [c for c in repo.calls if c.name == "region_novelty"]
    assert region_calls, "region_novelty should have run"
    assert region_calls[0].params["as_of"] == TS
    assert region_calls[0].params["as_of"] != "2016-12-10 15:01:21"


async def test_the_probability_starts_at_the_trigger_prior_not_the_score():
    orch, _, _ = orchestrator()
    emitter = CollectingEmitter()
    await orch.run(ALERT, emitter)

    started = emitter.of_type("run.started")[0]
    # A customer report is fitted at 0.75; the model's score on this transaction
    # is 0.40 and has nothing to do with where the ledger starts.
    assert started.payload["prior"] == 0.75
    assert started.payload["alert_risk_score"] is None, "the -1 sentinel is not a score"


async def test_the_graph_facts_pull_the_probability_below_its_prior():
    orch, _, _ = orchestrator()
    result = await orch.run(ALERT)
    assert result.answer.case.fraud_probability < 0.75


async def test_no_device_evidence_is_claimed_without_an_identity_record():
    """HHG-003 has no identity record, so no *graph* claim may mention a device.

    Scoped to graph-sourced evidence deliberately. A `document` item quotes the
    Fraud Policy, and §3a's own wording names a shared device profile as one of
    the things that would require a report — quoting a rule is not asserting a
    fact about this card.
    """
    orch, _, _ = orchestrator()
    result = await orch.run(ALERT)
    evidence = result.answer.case.evidence
    assert {e.ref for e in evidence}, "evidence must cite its queries"
    graph_claims = " ".join(e.claim for e in evidence if e.source is EvidenceSource.GRAPH).lower()
    assert "device" not in graph_claims or "identity record" in graph_claims


async def test_the_cardholder_is_not_blocked():
    """R7 is a bar, not a preference: 57 charges over 6 distinct months."""
    orch, _, _ = orchestrator()
    result = await orch.run(ALERT)
    actions = {a.action for a in result.answer.next_best_actions.final}
    assert Action.BLOCK_CARD not in actions
    assert Action.BLOCK_ALL_CARDS not in actions


async def test_a_case_is_opened_because_a_customer_disputed_a_charge():
    orch, _, _ = orchestrator()
    result = await orch.run(ALERT)
    actions = {a.action for a in result.answer.next_best_actions.final}
    assert Action.CREATE_CASE in actions


async def test_every_route_matches_the_table():
    orch, _, _ = orchestrator()
    result = await orch.run(ALERT)
    for phase in (result.answer.next_best_actions.initial, result.answer.next_best_actions.final):
        for rec in phase:
            assert rec.route in (Route.AUTO, Route.L1, Route.L2)
            assert rec.reason.strip(), f"{rec.action} has no reason"


async def test_the_instrumentation_is_real():
    orch, llm, _ = orchestrator()
    result = await orch.run(ALERT)
    # The hand-built golden file reports tokens: 0 because no model ran. An
    # agent run reporting 0 would be an obvious tell.
    assert result.answer.tokens > 0
    assert result.answer.tokens == llm.meter.tokens
    assert result.answer.tool_calls > 0
    assert result.answer.latency_s >= 0


async def test_all_four_agents_are_consulted():
    orch, llm, _ = orchestrator()
    await orch.run(ALERT)
    assert set(llm.purposes) == {"plan", "claims", "exonerate", "sar_narrative"}


async def test_the_assessment_prompt_carries_the_pattern_definitions():
    """The grounding that stops the misclassification measured against the real
    model: asked cold with these facts, gpt-5.4-mini called this card_testing."""
    from sentinel.llm.prompts import ASSESSMENT_SYSTEM

    flattened = " ".join(ASSESSMENT_SYSTEM.split())
    assert "RECURRING CHARGE, not card testing" in flattened
    assert "three or more TINY authorisations" in flattened
    for pattern in Pattern:
        assert pattern.value in flattened, f"{pattern.value} is not defined for the model"


async def test_the_assessment_prompt_carries_the_ledger_findings():
    orch, llm, _ = orchestrator()
    await orch.run(ALERT)
    prompt = llm.prompts["claims"]
    assert "53 prior charges" in prompt, "the exonerating fact must reach the model"
    assert "0.75" in prompt, "the prior it started from"


# ═══ the seams that only show at run time ════════════════════════════════════


async def test_final_deep_equals_initial_when_nothing_was_requested():
    """The validator makes any difference a hard error."""
    orch, _, _ = orchestrator()
    result = await orch.run(ALERT)
    nba = result.answer.next_best_actions
    if not result.answer.evidence_requests:
        assert nba.final == nba.initial
        assert nba.what_changed == "nothing"


async def test_at_most_one_evidence_round_is_ever_requested():
    orch, _, _ = orchestrator()
    result = await orch.run(ALERT)
    assert len(result.answer.evidence_requests) <= 1


async def test_the_requested_step_number_matches_the_stream():
    orch, _, _ = orchestrator()
    emitter = CollectingEmitter()
    result = await orch.run(ALERT, emitter)
    if result.answer.evidence_requests:
        asked_after = result.answer.evidence_requests[0].asked_after_step
        emitted = emitter.of_type("evidence.requested")[0].step
        assert asked_after == emitted


async def test_the_sar_flag_agrees_with_the_action_list():
    orch, _, _ = orchestrator()
    result = await orch.run(ALERT)
    files = Action.FILE_REPORT in {a.action for a in result.answer.next_best_actions.final}
    assert result.answer.sar.file is files
    assert result.answer.sar.reason.strip(), "a reason is required in both branches"


async def test_the_trajectory_is_emitted_for_the_sparkline():
    orch, _, _ = orchestrator()
    emitter = CollectingEmitter()
    await orch.run(ALERT, emitter)
    verdict = emitter.of_type("verdict.reached")[0]
    trajectory = verdict.payload["trajectory"]
    assert len(trajectory) == len(emitter.of_type("evidence.posted")) + 1
    assert trajectory[0] == pytest.approx(0.75, abs=0.01), "it starts at the prior"


async def test_a_dead_model_degrades_the_run_rather_than_ending_it():
    """Twenty answer files are the deliverable. An API hiccup must not cost one."""
    from sentinel.domain.errors import LlmUnavailable

    orch, llm, _ = orchestrator()

    async def boom(**_: Any):
        raise LlmUnavailable("upstream is down")

    llm.complete = boom  # type: ignore[assignment]
    result = await orch.run(ALERT)
    assert result.answer.case_id == "HHG-003"
    assert result.answer.case.summary, "the fallback summary still says something"


async def test_the_budget_stops_a_runaway_sweep():
    orch, _, _ = orchestrator()
    orch.settings.max_tool_calls_per_run = 3
    result = await orch.run(ALERT)
    assert result.answer.tool_calls <= 4, "the ceiling binds, with scope already spent"
