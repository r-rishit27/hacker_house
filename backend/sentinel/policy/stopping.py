"""Policy section 6: when the investigation has learned enough to decide.

Collaborators: ``PolicyConfig`` for the three constants, ``CaseState`` for the
probability and the verification response, and the orchestrator's loop, which
calls this after every evidence round. The independent-support count comes from
the evidence ledger, not from the state, because the loop asks before the state
for the next phase has been built.
"""

from __future__ import annotations

from dataclasses import dataclass

from sentinel.domain.enums import CustomerResponse
from sentinel.policy.config import DEFAULT_POLICY_CONFIG, PolicyConfig
from sentinel.policy.state import CaseState

#: The two responses that settle the question outright. ``no_reply`` does not:
#: it routes into R4 and the investigation continues.
#:
#: Only a *requested* response settles anything. Policy §6 says "a verification
#: response settles the question", and a customer's opening report is the
#: trigger, not a response — stopping on it would mean the eight
#: ``customer_report`` cases never ask the one question that could move them.
_SETTLING_RESPONSES = (CustomerResponse.DENIED, CustomerResponse.CONFIRMED)


@dataclass(frozen=True, slots=True)
class StopDecision:
    """Stop or continue, with the sentence that justifies it.

    ``reason`` is empty only when ``stop`` is False, so the UI can render the
    stopping rule verbatim beside the trajectory.
    """

    stop: bool
    reason: str


class StoppingPolicy:
    """Answers "is more evidence worth gathering?" — a pure function of two inputs.

    Confidence alone is never enough: both bands require corroboration, because a
    single signal driving the probability to 0.90 is exactly the case R1 exists
    to stop.
    """

    def __init__(self, config: PolicyConfig = DEFAULT_POLICY_CONFIG) -> None:
        self._config = config

    def should_stop(self, state: CaseState, independent_support: int) -> StopDecision:
        probability = state.fraud_probability
        if state.response_requested and state.customer_response in _SETTLING_RESPONSES:
            response = state.customer_response.value if state.customer_response else ""
            return StopDecision(
                True,
                "Policy 6: the verification response settled the question "
                f"(cardholder {response} the transaction).",
            )
        corroborated = independent_support >= self._config.min_independent_support
        if probability >= self._config.stop_high and corroborated:
            return StopDecision(
                True,
                f"Policy 6: probability {probability:.2f} is at or above 0.85 with "
                f"{independent_support} independent pieces of evidence.",
            )
        if probability <= self._config.stop_low and corroborated:
            return StopDecision(
                True,
                f"Policy 6: probability {probability:.2f} is at or below 0.15 with "
                f"{independent_support} independent pieces of evidence.",
            )
        return StopDecision(False, "")
