"""The error taxonomy.

Two rules this file exists to enforce:

1. **A failed lookup is not a missing vertex.** ``eval/validate.py`` currently
   swallows every exception into ``"{vtype} '{vid}' does not exist in the graph"``,
   so one transient auth hiccup reads as an invalid submission. ``VertexNotFound``
   and ``GraphUnavailable`` are different classes here and must stay different.
2. **A silent default is a bug.** A feature name that is not in the fitted table,
   or a trigger type with no prior, raises rather than posting nothing or quietly
   using 0.5.
"""

from __future__ import annotations

from typing import Any


class SentinelError(Exception):
    """Base for everything this system raises deliberately.

    Collaborators: the API's problem-detail handler reads ``code`` and
    ``http_status`` directly, so subclasses set them as class attributes.
    """

    code: str = "internal_error"
    http_status: int = 500

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context = context

    def to_problem(self) -> dict[str, Any]:
        """RFC 9457 problem detail, minus the fields only the API can fill."""
        return {
            "title": self.__class__.__name__,
            "status": self.http_status,
            "code": self.code,
            "detail": self.message,
            **self.context,
        }


# ── Graph ────────────────────────────────────────────────────────────────────


class GraphError(SentinelError):
    code = "graph_error"
    http_status = 502


class GraphUnavailable(GraphError):
    """The workspace could not be reached, or refused the request."""

    code = "graph_unavailable"
    http_status = 503


class GraphColdStart(GraphUnavailable):
    """The Savanna workspace is waking.

    Not an error condition: the first call after idle returns an HTML
    "Starting workspace" page instead of JSON and the workspace is ready about
    45 seconds later. The repository retries; this never reaches a user.
    """

    code = "graph_cold_start"


class VertexNotFound(GraphError):
    """The id genuinely is not in the graph — distinct from a failed lookup."""

    code = "vertex_not_found"
    http_status = 404


class QueryFailed(GraphError):
    """An installed query returned an error payload."""

    code = "query_failed"


# ── Evidence and policy ──────────────────────────────────────────────────────


class UnknownFeature(SentinelError):
    """A feature name that is not in the fitted likelihood table.

    Raised rather than ignored: a typo must never silently post nothing, because
    the probability would then be wrong in a way nothing detects.
    """

    code = "unknown_feature"


class UnknownTrigger(SentinelError):
    """A trigger type with no fitted prior.

    ``elt.json`` shipped without an ``analyst_request`` key, so the v1 ledger
    silently fell back to 0.5 — on HHG-014, the one analyst-request alert in the
    benchmark. An explicit prior is required.
    """

    code = "unknown_trigger"


class NoRouteDefined(SentinelError):
    """An action with no entry in the routing table."""

    code = "no_route_defined"


# ── LLM ──────────────────────────────────────────────────────────────────────


class LlmUnavailable(SentinelError):
    code = "llm_unavailable"
    http_status = 503


class StructuredOutputError(SentinelError):
    """The model's response did not satisfy the requested schema."""

    code = "structured_output_error"


# ── Run control ──────────────────────────────────────────────────────────────


class BudgetExceeded(SentinelError):
    """A run hit one of its ceilings: tool calls, evidence rounds, tokens, USD or wall clock."""

    code = "budget_exceeded"
    http_status = 409


class AnswerInvalid(SentinelError):
    """The assembled answer failed validation.

    The file is quarantined in ``runs/`` and never written to ``cases/``.
    """

    code = "answer_invalid"
    http_status = 422


# ── Permissions ──────────────────────────────────────────────────────────────


class PermissionDenied(SentinelError):
    """The principal's role may not execute this action at this route.

    The message names the required route and the roles that can approve, because
    "permission denied" alone is useless to an analyst.
    """

    code = "forbidden_route"
    http_status = 403
