"""The application services: permission, execution, approval, audit, retry.

Everything here sits between a controller and the domain. The controllers own
HTTP and nothing else; ``sentinel`` owns the fraud decisions and knows nothing
about a request. What is left — who may act, what that act did, who approved
it, and what happens when the same call arrives twice — lives in this package.

The wiring order matters once, and only once. ``ApprovalService`` and
``ActionExecutionService`` need each other: a denial enqueues an approval, and
an approval releases an execution. Build the approvals first, pass them to the
execution service, then call ``approvals.bind(executions)``.
"""

from api.services.approvals import (
    APPROVED,
    PENDING,
    REJECTED,
    ApprovalDecision,
    ApprovalExecutor,
    ApprovalPage,
    ApprovalService,
    Decision,
    approval_as_dict,
    parse_decision,
)
from api.services.audit import (
    OUTCOMES,
    AuditPage,
    AuditService,
    Outcome,
    audit_rows_as_dicts,
)
from api.services.execution import (
    EXECUTE_SCOPE,
    ActionExecutionService,
    ActionPlan,
    ActionRequest,
    AnswerSource,
    ApprovalQueue,
    DenialPolicy,
    FileAnswerSource,
    PlannedAction,
    execution_as_dict,
    load_alerts,
    parse_on_denied,
    parse_phase,
    require_answer,
)
from api.services.idempotency import IN_FLIGHT, IdempotencyService
from api.services.permissions import RoutePermissionPolicy

__all__ = [
    "APPROVED",
    "EXECUTE_SCOPE",
    "IN_FLIGHT",
    "OUTCOMES",
    "PENDING",
    "REJECTED",
    "ActionExecutionService",
    "ActionPlan",
    "ActionRequest",
    "AnswerSource",
    "ApprovalDecision",
    "ApprovalExecutor",
    "ApprovalPage",
    "ApprovalQueue",
    "ApprovalService",
    "AuditPage",
    "AuditService",
    "Decision",
    "DenialPolicy",
    "FileAnswerSource",
    "IdempotencyService",
    "Outcome",
    "PlannedAction",
    "RoutePermissionPolicy",
    "approval_as_dict",
    "audit_rows_as_dicts",
    "execution_as_dict",
    "load_alerts",
    "parse_decision",
    "parse_on_denied",
    "parse_phase",
    "require_answer",
]
