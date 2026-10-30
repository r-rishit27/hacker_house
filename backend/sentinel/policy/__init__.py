"""The Fraud Policy engine: rules R1-R10, section 3a, section 6 and the routing table.

No LLM, no I/O, no clock, no randomness lives under this package. Everything is
a pure function of ``CaseState``, which is what makes one unit test per rule
possible and what keeps the agent from talking itself past a policy bar.

``build_policy_engine()`` is the composition root; import that unless you are a
test injecting a single gate.
"""

from __future__ import annotations

from sentinel.policy.config import DEFAULT_POLICY_CONFIG, PolicyConfig
from sentinel.policy.engine import (
    PolicyEngine,
    PolicyOutcome,
    Recommendation,
    RecommendationSet,
    build_policy_engine,
)
from sentinel.policy.gates import GateLog, GateOutcome, PolicyGate, default_gates
from sentinel.policy.routing import RoutingTable
from sentinel.policy.sar import SarDecision, SarPolicy
from sentinel.policy.state import CaseState
from sentinel.policy.stopping import StopDecision, StoppingPolicy

__all__ = [
    "DEFAULT_POLICY_CONFIG",
    "CaseState",
    "GateLog",
    "GateOutcome",
    "PolicyConfig",
    "PolicyEngine",
    "PolicyGate",
    "PolicyOutcome",
    "Recommendation",
    "RecommendationSet",
    "RoutingTable",
    "SarDecision",
    "SarPolicy",
    "StopDecision",
    "StoppingPolicy",
    "build_policy_engine",
    "default_gates",
]
