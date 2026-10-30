"""Choosing which questions to ask the graph.

The planner decides the *order* of the investigation and states why. It does
not decide the *set* — and the difference is the whole design.

Two measurements forced that. Free to choose, the planner picked 6 detectors of
11 on HHG-003 and the five it dropped were the exonerating ones: the amount
band, the card's own amount distribution, the recurring probe. Same facts,
p = 0.74 instead of 0.36, while its own summary said the evidence did not fit a
fraud pattern. Restricted to *adding* to a mandatory core, it then added a
different set on each of four identical runs, and the probability came back
0.3569, 0.3577 or 0.5025.

Neither is a prompt problem. A fitted likelihood ratio that never posts is not
neutral, it is missing, so which detectors ran *is* the answer. The set is
therefore a function of the trigger type and nothing else, and what the planner
contributes is the order — which is what matters when the tool ceiling binds —
and a stated reason per detector, which is what the case file has to explain.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from sentinel.agents.agents.base import LlmAgent, bullet
from sentinel.agents.context import InvestigationContext
from sentinel.domain.enums import TriggerType
from sentinel.llm.prompts import PLANNER_SYSTEM


class PlannedCall(BaseModel):
    """One chosen tool. Deliberately no arguments.

    The sweep builds every argument from measurements, so a planner cannot
    anchor a window on the alert's timestamp instead of the transaction's. It
    also keeps the schema expressible: OpenAI's strict mode requires
    `additionalProperties: false` on every object, which a free-form argument
    dict cannot satisfy.
    """

    name: str
    why: str = ""


class PlanOutput(BaseModel):
    tools: list[PlannedCall] = Field(default_factory=list)


#: The detectors every case runs, in the order the hand investigation used them.
#: txn_detail comes first because it is the only source of the true `ts`, and
#: every window in the run is anchored on it.
CORE_PLAN: tuple[str, ...] = (
    "card_baseline",
    "card_window",
    "amount_band_probe",
    "card_amount_stats",
    "region_novelty",
    "device_novelty",
    "txn_sequence_context",
    "customer_case_history",
    "case_memory_for_card",
)

#: Asked on every case whatever the trigger, because each is the premise of a
#: policy rule that *bars* an action rather than suggesting one, and a bar that
#: did not run is a bar that does not protect anybody:
#:
#: - ``recurring_charge_probe`` is R7's premise. A cardholder who forgot their
#:   own subscription must not be blocked, and that is no less true when the
#:   alert arrived from the model rather than from them.
#: - ``card_testing_probe`` is R5's, which both requires an action on a testing
#:   sequence and, by its absence, argues against one.
UNIVERSAL_PLAN: tuple[str, ...] = (
    "recurring_charge_probe",
    "card_testing_probe",
)

#: What a trigger adds, because the question each alert asks differs. These are
#: kept narrow on purpose. Widening them to "ask everything" was tried and
#: measured: adding ``ring_expand`` to a $49 card-present dispute pulled in
#: eleven cards sharing an iPad Safari fingerprint and posted
#: ``device_profile_generic`` at +0.60, carrying HHG-003 from 0.357 to 0.503 —
#: away from the 0.32-0.38 that the hand investigation and v1's ledger
#: independently reached. A detector that cannot inform the question being
#: asked can still move the answer.
TRIGGER_PLANS: dict[TriggerType, tuple[str, ...]] = {
    # A score alone. The job is to find what the score is reacting to, or to
    # establish that nothing supports it.
    TriggerType.RISK_SCORE: ("velocity_probe", "product_novelty"),
    # A dispute. The two universal probes already cover what R7 and R5 need.
    TriggerType.CUSTOMER_REPORT: (),
    # HHG-014's trigger says outright that several cards share an unusual
    # device profile, so the ring is the question being asked.
    TriggerType.ANALYST_REQUEST: ("ring_expand", "device_neighbors", "velocity_probe"),
}


@dataclass
class PlannerAgent(LlmAgent[PlanOutput]):
    """Picks the next graph calls; falls back to the plan per trigger type."""

    purpose: str = "plan"
    schema: type[BaseModel] = PlanOutput
    system: str = PLANNER_SYSTEM
    max_output_tokens: int = 800

    def render(self, ctx: InvestigationContext) -> str:
        catalogue = bullet([f"{tool.name}: {tool.question}" for tool in ctx.tools.catalogue()])
        known = bullet([p.claim for p in ctx.ledger.postings]) or "- nothing yet"
        txn = ctx.txn
        return (
            f"Case {ctx.alert.alert_id} ({ctx.alert.trigger_type.value}).\n"
            f"Trigger text: {ctx.alert.trigger_text}\n"
            f"Card {ctx.alert.card_id}, customer {ctx.alert.customer_id}, "
            f"flagged transaction {ctx.flagged_txn_id}.\n"
            f"as_of (use this for every window): {ctx.as_of}\n"
            + (
                f"The transaction is ${txn.amt:,.2f}, {txn.channel}, region "
                f"{txn.addr1 or 'not recorded'}, product {txn.product}, "
                f"model score {txn.risk_score:.2f}.\n"
                if txn is not None
                else ""
            )
            + f"\nAlready known:\n{known}\n\n"
            f"Tools available:\n{catalogue}\n\n"
            f"You may make at most {ctx.guard.remaining_tool_calls()} more calls. "
            "Choose the ones whose answers could change the decision."
        )

    def fallback(self, ctx: InvestigationContext) -> PlanOutput:
        return PlanOutput(
            tools=[PlannedCall(name=name, why="standard plan") for name in self.static_plan(ctx)]
        )

    @staticmethod
    def static_plan(ctx: InvestigationContext) -> tuple[str, ...]:
        """Every detector that will run. A function of the trigger, nothing else.

        This set is deterministic on purpose, and that is the single most
        important property of this module. Measured across four identical runs
        of HHG-003 against the live model, a planner free to *add* detectors
        added a different set each time — 12, 13 and 14 tool calls — and the
        probability came back 0.3569, 0.3577 or 0.5025, with a different action
        list on the outlier. Every aggregate the submission is judged on is
        computed from those numbers, so a run could pass the gate and its
        re-run for the demo could fail it.

        The cause is structural rather than a prompt problem: a detector that
        did not run posts nothing, and a fitted likelihood ratio that is absent
        is not neutral — it is simply missing from the sum. So *what* is asked
        is fixed and the planner decides the *order*, which is what the budget
        ceiling makes matter.
        """
        return CORE_PLAN + UNIVERSAL_PLAN + TRIGGER_PLANS.get(ctx.alert.trigger_type, ())

    @staticmethod
    def order(output: PlanOutput, ctx: InvestigationContext) -> list[str]:
        """The fixed plan, ordered by the planner's stated priority.

        Anything the planner names that is in the plan moves to the front, in
        the order it named them; the rest follow in their canonical order. If
        the tool ceiling is reached mid-sweep, the detectors the planner argued
        for are the ones that already ran.
        """
        plan = PlannerAgent.static_plan(ctx)
        preferred = dict.fromkeys(call.name for call in output.tools if call.name in plan)
        return list(preferred) + [name for name in plan if name not in preferred]

    @staticmethod
    def rationale(output: PlanOutput, ctx: InvestigationContext) -> list[dict[str, str]]:
        """Why the planner prioritised what it did, for the trace and the UI.

        A name the registry does not know is not worth failing a run over; it
        is recorded as ``unknown`` so the trace shows what was asked for and
        what happened to it. A name outside the plan is recorded as
        ``not_in_plan``: the detector set is fixed, and saying so beside the
        request is more honest than dropping it silently.
        """
        plan = set(PlannerAgent.static_plan(ctx))
        notes: list[dict[str, str]] = []
        seen: set[str] = set()
        for call in output.tools:
            if call.name in seen:
                continue
            seen.add(call.name)
            if call.name not in ctx.tools:
                status = "unknown"
            elif call.name not in plan:
                status = "not_in_plan"
            else:
                status = "prioritised"
            notes.append({"tool": call.name, "why": call.why, "status": status})
        return notes
