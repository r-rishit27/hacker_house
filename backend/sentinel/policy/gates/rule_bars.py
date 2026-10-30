"""The three bars the Fraud Policy states as prohibitions: R1, R7 and R10.

Collaborators: ``PolicyConfig`` (R1's 0.70), ``CaseState``, ``RecommendationSet``.
Each of these is written in Guide.md as something the agent must *not* do, so
each is enforced after the rules rather than trusted to them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sentinel.domain.enums import Action
from sentinel.policy.config import DEFAULT_POLICY_CONFIG, PolicyConfig
from sentinel.policy.gates.base import GateOutcome, PolicyGate
from sentinel.policy.state import CaseState

if TYPE_CHECKING:  # pragma: no cover - import cycle: the engine owns the set
    from sentinel.policy.engine import RecommendationSet

_BLOCKS = (Action.BLOCK_CARD, Action.BLOCK_ALL_CARDS)


class R1NoWeakBlockGate(PolicyGate):
    """R1: no block while the case rests on one signal below 0.70 and unverified.

    "Blocking a legitimate customer on one signal is a policy breach." Half the
    case pack is legitimate, so this bar protects more cases than any other.
    """

    name: ClassVar[str] = "R1NoWeakBlock"

    def __init__(self, config: PolicyConfig = DEFAULT_POLICY_CONFIG) -> None:
        self._config = config

    def apply(self, recs: RecommendationSet, state: CaseState) -> GateOutcome:
        if not self._weak_single_signal(state):
            return self._silent()
        removed = recs.remove_any(*_BLOCKS)
        if not removed:
            return self._silent()
        return GateOutcome(
            self.name,
            removed=removed,
            reason=(
                f"R1: the case rests on a single signal and assessed probability "
                f"{state.fraud_probability:.2f} is below 0.70 with no customer response, "
                f"so no block may be recommended yet"
            ),
        )

    def _weak_single_signal(self, state: CaseState) -> bool:
        return (
            state.rests_on_one_signal
            and state.fraud_probability < self._config.verify_before_block_threshold
            and not state.has_customer_response
        )


class R7NeverBlockRecurringGate(PolicyGate):
    """R7: a charge matching the cardholder's own recurring pattern is never blocked.

    A bar, not a preference — it survives a denial, because a cardholder who
    disputes their own monthly subscription is mistaken, not defrauded, and R2's
    ``BLOCK_CARD`` would punish them for it.
    """

    name: ClassVar[str] = "R7NeverBlockRecurring"

    def apply(self, recs: RecommendationSet, state: CaseState) -> GateOutcome:
        if not state.recurring_match:
            return self._silent()
        removed = recs.remove_any(Action.BLOCK_CARD)
        if not removed:
            return self._silent()
        return GateOutcome(
            self.name,
            removed=removed,
            reason=(
                "R7: the disputed charge matches the cardholder's own recurring "
                "pattern, so it is never blocked"
            ),
        )


class R10BlockAllCardsGate(PolicyGate):
    """R10: ``BLOCK_ALL_CARDS`` is unreachable without two compromised cards.

    The alternative qualifier is confirmed credential compromise. Nothing else
    opens this door — not exposure, not probability, not a denial.
    """

    name: ClassVar[str] = "R10BlockAllCards"

    def apply(self, recs: RecommendationSet, state: CaseState) -> GateOutcome:
        if self._qualifies(state):
            return self._silent()
        removed = recs.remove_any(Action.BLOCK_ALL_CARDS)
        if not removed:
            return self._silent()
        return GateOutcome(
            self.name,
            removed=removed,
            reason=(
                f"R10: {state.cards_with_confirmed_fraud} card(s) show confirmed fraud and "
                f"credentials are not confirmed compromised, so every card cannot be blocked"
            ),
        )

    @staticmethod
    def _qualifies(state: CaseState) -> bool:
        return state.cards_with_confirmed_fraud >= 2 or state.credentials_compromised
