"""Section 3a: when a suspicious activity report is filed, and why.

Collaborators: ``PolicyConfig`` for the $1,000 threshold, ``CaseState`` for the
four inputs, and ``PolicyEngine``, which adds ``FILE_REPORT`` on exactly this
decision so the answer file's ``sar.file`` and the action list can never
disagree.

The section 3a gate **dominates R2**. R2 read alone — "add FILE_REPORT if
exposure exceeds $1,000 or the case connects to a shared device profile or
another card's fraud" — has no confirmation gate and over-files. Section 3a adds
"fraud is confirmed or strongly suspected" and a third trigger, and that is the
reading implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass

from sentinel.domain.enums import Verdict
from sentinel.policy.config import DEFAULT_POLICY_CONFIG, PolicyConfig
from sentinel.policy.state import CaseState


@dataclass(frozen=True, slots=True)
class SarDecision:
    """Whether to file, the narrative reason, and the triggers behind it.

    ``file`` and ``reason`` leave ``SarPolicy`` as one object so they cannot
    drift apart. ``reason`` is populated in **both** branches: Guide.md lists it
    as required and ``eval/validate.py`` never checks it, so an empty string
    would ship unnoticed.
    """

    file: bool
    reason: str
    triggers: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {"file": self.file, "reason": self.reason, "triggers": list(self.triggers)}


class SarPolicy:
    """Evaluates section 3a over a ``CaseState``.

    Pure: no I/O, no LLM, no clock. The same state always yields the same
    decision, which is what lets the initial and final recommendations be
    compared.
    """

    def __init__(self, config: PolicyConfig = DEFAULT_POLICY_CONFIG) -> None:
        self._config = config

    def evaluate(self, state: CaseState) -> SarDecision:
        """File only when fraud is confirmed or strongly suspected AND a trigger holds."""
        strongly_suspected = self._strongly_suspected(state)
        triggers = self._triggers(state)
        if strongly_suspected and triggers:
            return SarDecision(
                file=True,
                reason="3a: fraud confirmed or strongly suspected and " + "; ".join(triggers) + ".",
                triggers=tuple(triggers),
            )
        return SarDecision(
            file=False,
            reason="3a: no report. " + "; ".join(self._refusals(state, strongly_suspected)) + ".",
            triggers=tuple(triggers),
        )

    def _strongly_suspected(self, state: CaseState) -> bool:
        return (
            state.verdict is Verdict.FRAUD
            or state.fraud_probability >= self._config.verify_before_block_threshold
        )

    def _triggers(self, state: CaseState) -> list[str]:
        """The three section 3a conditions, one of which must hold."""
        triggers: list[str] = []
        if state.exposure_usd > self._config.sar_exposure_threshold:
            triggers.append(f"exposure ${state.exposure_usd:,.2f} exceeds $1,000")
        if state.shared_origin:
            triggers.append(
                f"the activity connects to {state.shared_origin_kind or 'a shared origin'}"
            )
        if state.other_customer_fraud:
            triggers.append("the activity connects to another customer's fraud")
        if state.undocumented_pattern:
            triggers.append("the pattern is coordinated and undocumented (R9)")
        return triggers

    def _refusals(self, state: CaseState, strongly_suspected: bool) -> list[str]:
        """Why no report — every failing condition, not just the first."""
        bits: list[str] = []
        if not strongly_suspected:
            bits.append(
                f"fraud is neither confirmed nor strongly suspected "
                f"(probability {state.fraud_probability:.2f})"
            )
        if state.exposure_usd <= self._config.sar_exposure_threshold:
            bits.append(f"exposure ${state.exposure_usd:,.2f} is at or below $1,000")
        if not (state.shared_origin or state.other_customer_fraud or state.undocumented_pattern):
            bits.append(
                "the activity connects to no shared device profile, region "
                "cluster or other customer's fraud"
            )
        return bits
