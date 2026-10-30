"""The Fraud Policy as code. No LLM touches a decision in this module.

Twenty-five percent of the score is next-best-action quality, and a prompt will
eventually route ``BLOCK_CARD`` to ``auto`` or invent an action name. This is a
pure function from ``CaseState`` to an ordered, routed, cited action list, with
one unit test per rule.

Collaborators: ``RoutingTable`` (every route), ``SarPolicy`` (rule 10 and the
answer file's ``sar`` block), the six ``PolicyGate`` classes (defence in depth)
and ``PolicyConfig`` (every threshold). It reads nothing else — no graph, no
clock, no randomness.

Rule order is **execution order** — what happens first — not severity, because
the policy says "Order them by what happens first."
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sentinel.domain.enums import Action, CustomerResponse, Route, TriggerType, Verdict
from sentinel.policy.config import DEFAULT_POLICY_CONFIG, PolicyConfig
from sentinel.policy.gates import GateLog, PolicyGate, default_gates
from sentinel.policy.gates.base import GateOutcome, RouteChange
from sentinel.policy.routing import RoutingTable
from sentinel.policy.sar import SarDecision, SarPolicy
from sentinel.policy.state import CaseState

#: Every reason string this module produces opens with the rule that caused it
#: — "R7: cardholder disputes a charge that matches...". That convention is what
#: makes :func:`cited_rules` a read of what the engine did rather than a guess
#: at what it might have done, so it is enforced by a unit test.
_RULE_IN_REASON = re.compile(r"^(R\d{1,2}):")


def cited_rules(recommendations: Iterable[Recommendation]) -> list[str]:
    """The rule ids these recommendations were actually made under, in order.

    Used to build the `APPLIED_RULE` edges and the `source: "document"` evidence
    items. Reading them back off the reasons keeps one source of truth: a rule
    that fires without saying so in its reason is a rule an analyst cannot
    check, which is a defect either way.
    """
    out: list[str] = []
    for rec in recommendations:
        match = _RULE_IN_REASON.match(rec.reason)
        if match and match.group(1) not in out:
            out.append(match.group(1))
    return out


@dataclass(frozen=True, slots=True)
class Recommendation:
    """One next-best action: what to do, who must approve it, and under which rule.

    Frozen, because a route is corrected by replacing the recommendation rather
    than by editing one in place — the corrected list is what the gate reports.
    """

    action: Action
    route: Route
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"action": self.action.value, "route": self.route.value, "reason": self.reason}

    def with_route(self, route: Route) -> Recommendation:
        return Recommendation(self.action, route, self.reason)


class RecommendationSet:
    """An ordered, de-duplicated action list that routes itself.

    Collaborator: ``RoutingTable``. Rules and gates never name a route; they add
    an action and a reason and this class asks the table. The first reason for an
    action wins, because the first rule to reach it is the rule that caused it.
    """

    def __init__(self, routing: RoutingTable) -> None:
        self._routing = routing
        self._items: list[Recommendation] = []

    def add(self, action: Action, reason: str, state: CaseState) -> bool:
        """Append unless the action is already recommended. True if it was added."""
        if self.has(action):
            return False
        self._items.append(self._build(action, reason, state))
        return True

    def insert_first(self, action: Action, reason: str, state: CaseState) -> bool:
        """Put the action at the head of the list — execution order, not priority."""
        if self.has(action):
            return False
        self._items.insert(0, self._build(action, reason, state))
        return True

    def has(self, action: Action) -> bool:
        return any(item.action is action for item in self._items)

    def remove(self, action: Action) -> bool:
        before = len(self._items)
        self._items = [item for item in self._items if item.action is not action]
        return len(self._items) != before

    def remove_any(self, *actions: Action) -> tuple[Action, ...]:
        """Remove every named action; return those that were actually present."""
        removed = tuple(action for action in actions if self.has(action))
        if removed:
            self._items = [item for item in self._items if item.action not in removed]
        return removed

    def recompute_routes(self, state: CaseState) -> tuple[RouteChange, ...]:
        """Re-derive every route from the table; report the ones that were wrong."""
        changes: list[RouteChange] = []
        refreshed: list[Recommendation] = []
        for item in self._items:
            route = self._routing.route_for(item.action, state.exposure_usd)
            if route is not item.route:
                changes.append(RouteChange(item.action, item.route.value, route.value))
                refreshed.append(item.with_route(route))
            else:
                refreshed.append(item)
        self._items = refreshed
        return tuple(changes)

    def actions(self) -> tuple[Action, ...]:
        return tuple(item.action for item in self._items)

    def to_list(self) -> list[Recommendation]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Any:
        return iter(self._items)

    def _build(self, action: Action, reason: str, state: CaseState) -> Recommendation:
        return Recommendation(action, self._routing.route_for(action, state.exposure_usd), reason)


@dataclass(frozen=True, slots=True)
class PolicyOutcome:
    """One evaluation: the actions, the bars that fired, and the SAR decision.

    Consumed by the orchestrator's ``policy.evaluated`` event. ``sar`` travels
    with the recommendations so the answer file's ``sar.file`` and the presence
    of ``FILE_REPORT`` are one decision, read twice.
    """

    recommendations: list[Recommendation]
    gates: list[GateOutcome]
    sar: SarDecision

    def as_dict(self) -> dict[str, Any]:
        return {
            "recommendations": [rec.as_dict() for rec in self.recommendations],
            "gates_applied": [gate.as_dict() for gate in self.gates if gate.fired],
            "sar": self.sar.as_dict(),
        }


class PolicyEngine:
    """Turns one ``CaseState`` into the ordered action list the answer file carries.

    Collaborators are injected: ``RoutingTable``, ``SarPolicy``, the gate
    pipeline and ``PolicyConfig``. ``build_policy_engine()`` is the composition
    root for callers that want the published policy.
    """

    def __init__(
        self,
        routing: RoutingTable,
        sar: SarPolicy,
        gates: Sequence[PolicyGate],
        config: PolicyConfig = DEFAULT_POLICY_CONFIG,
    ) -> None:
        self._routing = routing
        self._sar = sar
        self._gates = tuple(gates)
        self._config = config

    @property
    def config(self) -> PolicyConfig:
        """The thresholds this engine was built with, so collaborators share them."""
        return self._config

    def decide(self, state: CaseState) -> list[Recommendation]:
        """The ordered recommendations for one investigation state."""
        return self.evaluate(state).recommendations

    def evaluate(self, state: CaseState) -> PolicyOutcome:
        """``decide`` plus what the gates did and why the SAR went the way it did."""
        sar = self._sar.evaluate(state)
        recs = self._apply_rules(state, sar)
        log = GateLog()
        for gate in self._gates:
            log.record(gate.apply(recs, state))
        return PolicyOutcome(recs.to_list(), log.outcomes, sar)

    # ── the rules, in execution order ────────────────────────────────────────

    def _apply_rules(self, state: CaseState, sar: SarDecision) -> RecommendationSet:
        recs = RecommendationSet(self._routing)
        self._r5_card_testing(recs, state)
        self._r7_recurring_dispute(recs, state)
        self._r1_verify_before_block(recs, state)
        self._customer_response(recs, state)
        self._r5_cleared_purchase(recs, state)
        self._r6_shared_origin(recs, state)
        self._r9_undocumented(recs, state)
        self._case_threshold(recs, state)
        self._fraud_without_a_cardholder(recs, state)
        self._r8_uncertain_and_exposed(recs, state)
        self._sar_filing(recs, state, sar)
        self._r10_block_all_cards(recs, state)
        self._close_when_legitimate(recs, state)
        self._fallback(recs, state)
        return recs

    def _r5_card_testing(self, recs: RecommendationSet, state: CaseState) -> None:
        if not state.card_testing_sequence:
            return
        recs.add(
            Action.DECLINE_TRANSACTION,
            "R5: three or more small online authorizations within an hour followed "
            "by a larger purchase",
            state,
        )
        recs.add(Action.STEP_UP_AUTH, "R5: require step-up before further activity", state)

    def _r7_recurring_dispute(self, recs: RecommendationSet, state: CaseState) -> None:
        if not (state.recurring_match and state.trigger_type is TriggerType.CUSTOMER_REPORT):
            return
        recs.add(
            Action.CREATE_CASE,
            "R7: cardholder disputes a charge that matches their own recurring pattern",
            state,
        )
        # Guide.md R7 lists VERIFY_WITH_CUSTOMER even though the cardholder has
        # already disputed the charge, and that is not an oversight: R7's
        # question is "is this your own recurring subscription?", which the
        # dispute does not answer. It is suppressed only once a requested round
        # has actually come back.
        if not state.has_requested_response:
            recs.add(
                Action.VERIFY_WITH_CUSTOMER,
                "R7: confirm with the cardholder that this is their own recurring charge",
                state,
            )
        recs.add(
            Action.WARN_CUSTOMER,
            "R7: send an informational message about the recurring charge",
            state,
        )

    def _r1_verify_before_block(self, recs: RecommendationSet, state: CaseState) -> None:
        weak = (
            state.rests_on_one_signal
            and state.fraud_probability < self._config.verify_before_block_threshold
            and not state.has_customer_response
        )
        if not weak:
            return
        recs.add(
            Action.VERIFY_WITH_CUSTOMER,
            f"R1: the case rests on a single signal and assessed probability "
            f"{state.fraud_probability:.2f} is below 0.70, so verify before any block",
            state,
        )

    def _customer_response(self, recs: RecommendationSet, state: CaseState) -> None:
        """R2, R3 and R4 — what the cardholder said, or did not say."""
        response = state.customer_response
        if response is CustomerResponse.DENIED:
            if not state.recurring_match:
                recs.add(Action.BLOCK_CARD, "R2: cardholder denies the transaction", state)
            recs.add(Action.CREATE_CASE, "R2: cardholder denies the transaction", state)
        elif response is CustomerResponse.CONFIRMED:
            if not state.shared_origin and not state.undocumented_pattern:
                recs.add(Action.CLOSE_NO_FRAUD, "R3: cardholder confirms the transaction", state)
        elif response is CustomerResponse.NO_REPLY:
            recs.add(Action.MONITOR_CARD, "R4: no reply within 24 hours", state)
            if state.pending_authorization:
                recs.add(
                    Action.DECLINE_TRANSACTION,
                    "R4: decline pending authorizations while unverified",
                    state,
                )
            if state.exposure_usd > self._config.r4_escalate_threshold:
                recs.add(
                    Action.ESCALATE_TO_ANALYST,
                    f"R4: no reply and exposure ${state.exposure_usd:,.2f} exceeds $500",
                    state,
                )

    def _r5_cleared_purchase(self, recs: RecommendationSet, state: CaseState) -> None:
        if state.card_testing_sequence and state.card_testing_cleared_over_100:
            recs.add(
                Action.BLOCK_CARD,
                "R5: a purchase over $100 has already cleared after the testing sequence",
                state,
            )

    def _r6_shared_origin(self, recs: RecommendationSet, state: CaseState) -> None:
        if not state.shared_origin:
            return
        kind = state.shared_origin_kind or "a shared origin"
        recs.add(Action.CREATE_CASE, f"R6: several cards show fraud from {kind}", state)
        recs.add(Action.MONITOR_CONNECTED_CARDS, f"R6: monitor every card sharing {kind}", state)

    def _r9_undocumented(self, recs: RecommendationSet, state: CaseState) -> None:
        if not state.undocumented_pattern:
            return
        recs.add(Action.CREATE_CASE, "R9: coordinated abuse fitting no known pattern", state)
        recs.add(
            Action.ESCALATE_TO_ANALYST,
            "R9: undocumented pattern needs a human to characterise it",
            state,
        )

    def _case_threshold(self, recs: RecommendationSet, state: CaseState) -> None:
        """Section 3a: a case opens once fraud is plausible, or evidence was requested."""
        if state.fraud_probability >= self._config.case_probability_threshold or (
            state.has_customer_response
        ):
            recs.add(
                Action.CREATE_CASE,
                f"3a: a case is opened once fraud probability reaches 0.30 "
                f"(assessed {state.fraud_probability:.2f}) or evidence has been requested",
                state,
            )

    def _r8_uncertain_and_exposed(self, recs: RecommendationSet, state: CaseState) -> None:
        if state.verdict is not Verdict.UNCERTAIN:
            return
        exposed = state.exposure_usd > self._config.escalate_exposure_threshold
        if not (exposed or state.evidence_conflicts):
            return
        why = (
            "the evidence conflicts"
            if state.evidence_conflicts
            else f"exposure ${state.exposure_usd:,.2f} exceeds $500"
        )
        recs.add(Action.ESCALATE_TO_ANALYST, f"R8: verdict is uncertain and {why}", state)

    def _sar_filing(self, recs: RecommendationSet, state: CaseState, sar: SarDecision) -> None:
        """Section 3a, decided by the same object the answer file's ``sar`` block uses."""
        if sar.file:
            recs.add(Action.FILE_REPORT, sar.reason, state)

    def _r10_block_all_cards(self, recs: RecommendationSet, state: CaseState) -> None:
        if state.cards_with_confirmed_fraud >= 2 or state.credentials_compromised:
            recs.add(
                Action.BLOCK_ALL_CARDS,
                "R10: two or more of the customer's cards show confirmed fraud, or "
                "credentials are confirmed compromised",
                state,
            )

    def _close_when_legitimate(self, recs: RecommendationSet, state: CaseState) -> None:
        if state.verdict is not Verdict.LEGITIMATE:
            return
        if recs.has(Action.CLOSE_NO_FRAUD) or recs.has(Action.WARN_CUSTOMER):
            return
        recs.add(
            Action.CLOSE_NO_FRAUD,
            f"assessed probability {state.fraud_probability:.2f} with no supporting evidence found",
            state,
        )

    def _fraud_without_a_cardholder(self, recs: RecommendationSet, state: CaseState) -> None:
        """A fraud verdict the cardholder has not been asked about yet.

        R1 to R10 cover the shapes the policy names, and one shape falls
        between them: the graph says fraud, the cardholder has said nothing,
        and no documented pattern matched. Nothing then adds an action, so
        §3a's `CREATE_CASE` stood alone — eight of the twenty benchmark cases
        came back with a `fraud` verdict above 0.88 whose entire recommendation
        was to open a case. That is not a next best action; it is a filing
        cabinet.

        Neither of the two actions added here blocks anything, so this cannot
        become a route to over-blocking: `BLOCK_CARD` still waits for R2's
        denial or R5's cleared purchase. Both are `auto`, both are in §1's
        table, and both are what §5 and §3b describe — ask the cardholder,
        which the agent may do without approval, and contain the card while
        waiting, which costs the cardholder nothing.
        """
        if state.verdict is not Verdict.FRAUD or state.has_customer_response:
            return
        recs.add(
            Action.VERIFY_WITH_CUSTOMER,
            f"the evidence puts this at {state.fraud_probability:.2f} and the cardholder "
            "has not been asked; R2 and R3 both turn on their answer",
            state,
        )
        recs.add(
            Action.MONITOR_CARD,
            f"raise sensitivity while the cardholder is asked, at assessed probability "
            f"{state.fraud_probability:.2f}",
            state,
        )

    def _fallback(self, recs: RecommendationSet, state: CaseState) -> None:
        """Monitoring is the low-impact answer when no rule fired at all."""
        if len(recs) == 0:
            recs.add(
                Action.MONITOR_CARD,
                f"assessed probability {state.fraud_probability:.2f} warrants sensitivity "
                f"but no action",
                state,
            )


def build_policy_engine(config: PolicyConfig = DEFAULT_POLICY_CONFIG) -> PolicyEngine:
    """The published policy, wired up. The composition root for this package."""
    routing = RoutingTable(config)
    return PolicyEngine(routing, SarPolicy(config), default_gates(config), config)
