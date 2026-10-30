"""The gate pipeline: six hard bars, in the order they must run.

Order matters twice. ``ReportNeedsCaseGate`` runs after the removal gates so it
sees the final ``FILE_REPORT``, and ``RouteRecomputeGate`` runs last so it also
routes whatever the earlier gates inserted.
"""

from __future__ import annotations

from sentinel.policy.config import DEFAULT_POLICY_CONFIG, PolicyConfig
from sentinel.policy.gates.base import GateLog, GateOutcome, PolicyGate, RouteChange
from sentinel.policy.gates.coherence import (
    CloseNoFraudExclusivityGate,
    ReportNeedsCaseGate,
    RouteRecomputeGate,
)
from sentinel.policy.gates.rule_bars import (
    R1NoWeakBlockGate,
    R7NeverBlockRecurringGate,
    R10BlockAllCardsGate,
)

__all__ = [
    "CloseNoFraudExclusivityGate",
    "GateLog",
    "GateOutcome",
    "PolicyGate",
    "R1NoWeakBlockGate",
    "R7NeverBlockRecurringGate",
    "R10BlockAllCardsGate",
    "ReportNeedsCaseGate",
    "RouteChange",
    "RouteRecomputeGate",
    "default_gates",
]


def default_gates(config: PolicyConfig = DEFAULT_POLICY_CONFIG) -> tuple[PolicyGate, ...]:
    """The published pipeline. ``PolicyEngine`` takes this unless a test injects less."""
    return (
        R1NoWeakBlockGate(config),
        R7NeverBlockRecurringGate(),
        R10BlockAllCardsGate(),
        ReportNeedsCaseGate(),
        CloseNoFraudExclusivityGate(),
        RouteRecomputeGate(),
    )
