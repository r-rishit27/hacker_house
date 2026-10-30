"""The investigation state the policy reads, and nothing else.

Collaborators: built by the orchestration layer from the evidence ledger and the
graph tools (task P13's ``CaseStateBuilder``), consumed by ``PolicyEngine``,
``SarPolicy``, ``StoppingPolicy`` and every gate. It holds no references back —
the policy must stay a pure function of this object.

Field names are the nineteen from v1 ``sentinel/policy.py`` verbatim. They are
load-bearing: the ported rule tests construct states by keyword and the SSE
payload names them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

from sentinel.domain.enums import CustomerResponse, Pattern, TriggerType, Verdict

_E = TypeVar("_E", bound=Enum)


def _coerce(value: Any, enum_cls: type[_E], field_name: str) -> _E:
    """Accept either an enum member or its verbatim string value.

    The policy compares enum members internally, but callers — the ported tests,
    a JSON payload, an LLM's structured output — hand over the string form. An
    unrecognised value raises rather than defaulting: a silent ``uncertain`` here
    would change which rules fire and nothing downstream would notice.
    """
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except ValueError as exc:
        allowed = ", ".join(repr(member.value) for member in enum_cls)
        raise ValueError(f"{field_name}={value!r} is not one of {allowed}") from exc


@dataclass(frozen=True)
class CaseState:
    """Everything the policy needs about one investigation at one moment.

    Frozen: the initial and the final recommendation are two evaluations over two
    states, and a rule that mutated the state would make the pair incomparable.
    """

    fraud_probability: float
    exposure_usd: float = 0.0
    verdict: Verdict = Verdict.UNCERTAIN

    trigger_type: TriggerType = TriggerType.RISK_SCORE
    customer_response: CustomerResponse | None = None
    #: Whether the response above came back from a round the agent *asked for*,
    #: as opposed to arriving with the trigger. Both are the cardholder's word
    #: and R2/R3/R4 treat them identically — a denial is a denial. The
    #: difference matters in exactly two places: R7 still wants to ask whether a
    #: disputed charge is the cardholder's own subscription (a different
    #: question from "did you make this"), and Policy §6 stops on "a
    #: verification response", which a customer's opening report is not.
    response_requested: bool = False

    #: How many independent evidence groups moved the probability. R1 turns on
    #: whether the case rests on a *single* signal, so leaving this at its
    #: default of 1 strips ``BLOCK_CARD`` from every case under p = 0.70 —
    #: the builder must set it from ``ledger.independent_support()``.
    independent_signals: int = 1

    pattern: Pattern = Pattern.NONE
    card_testing_sequence: bool = False
    card_testing_cleared_over_100: bool = False
    pending_authorization: bool = False

    recurring_match: bool = False
    shared_origin: bool = False
    shared_origin_kind: str = ""
    connected_card_ids: list[str] = field(default_factory=list)
    other_customer_fraud: bool = False

    cards_with_confirmed_fraud: int = 0
    credentials_compromised: bool = False

    evidence_conflicts: bool = False
    undocumented_pattern: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "verdict", _coerce(self.verdict, Verdict, "verdict"))
        object.__setattr__(
            self, "trigger_type", _coerce(self.trigger_type, TriggerType, "trigger_type")
        )
        object.__setattr__(self, "pattern", _coerce(self.pattern, Pattern, "pattern"))
        if self.customer_response is not None:
            object.__setattr__(
                self,
                "customer_response",
                _coerce(self.customer_response, CustomerResponse, "customer_response"),
            )
        object.__setattr__(self, "connected_card_ids", list(self.connected_card_ids))
        if not 0.0 <= self.fraud_probability <= 1.0:
            raise ValueError(f"fraud_probability={self.fraud_probability!r} is outside [0, 1]")
        if self.exposure_usd < 0.0:
            raise ValueError(f"exposure_usd={self.exposure_usd!r} is negative")

    @property
    def has_customer_response(self) -> bool:
        """True once the cardholder's position is known, whatever told us.

        On the eight ``customer_report`` alerts that position is "I never made
        this purchase", known at step zero. R1 exists to stop a block resting
        on one weak signal; a cardholder's denial is not that, so R1 stands
        down here whichever way the denial arrived.
        """
        return self.customer_response is not None

    @property
    def has_requested_response(self) -> bool:
        """True only for a reply to a round the agent actually asked for."""
        return self.customer_response is not None and self.response_requested

    @property
    def rests_on_one_signal(self) -> bool:
        """R1's premise: a single evidence group is carrying the probability."""
        return self.independent_signals <= 1
