"""Answer validation: the gate between a generated answer and ``cases/``.

``AnswerValidator`` is pure and runs everywhere. ``GraphIdentityChecker`` needs a
repository and runs wherever the graph is reachable.
"""

from __future__ import annotations

from sentinel.validation.graph_check import GraphIdentityChecker, VertexReader
from sentinel.validation.rules import (
    EvidenceRequestRule,
    LegitimateCaseRule,
    RoutingRule,
    SarConsistencyRule,
    ShapeRule,
    ValidationRule,
    default_rules,
)
from sentinel.validation.validator import AnswerValidator, Finding, ValidationReport

__all__ = [
    "AnswerValidator",
    "EvidenceRequestRule",
    "Finding",
    "GraphIdentityChecker",
    "LegitimateCaseRule",
    "RoutingRule",
    "SarConsistencyRule",
    "ShapeRule",
    "ValidationReport",
    "ValidationRule",
    "VertexReader",
    "default_rules",
]
