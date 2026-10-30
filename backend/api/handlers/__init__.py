"""The effects: one handler family per group of related actions.

Kept apart from ``api.services`` because the services decide *whether* an
action may happen and record that it did, while a handler only knows *what*
happening means. Nothing here may be called directly — ``ActionExecutionService``
is the single door, which is what makes the audit log complete.
"""

from api.handlers.actions import (
    ActionHandler,
    ActionHandlerRegistry,
    ActionOutcome,
    AuthorizationDecisionHandler,
    BlockCardHandler,
    CloseCaseHandler,
    CreateCaseHandler,
    EscalateHandler,
    ExecutionContext,
    FileReportHandler,
    GenerateReportHandler,
    MonitorHandler,
    Phase,
    SendMessageHandler,
    StepUpHandler,
    applied_rules,
    build_action_handlers,
)

__all__ = [
    "ActionHandler",
    "ActionHandlerRegistry",
    "ActionOutcome",
    "AuthorizationDecisionHandler",
    "BlockCardHandler",
    "CloseCaseHandler",
    "CreateCaseHandler",
    "EscalateHandler",
    "ExecutionContext",
    "FileReportHandler",
    "GenerateReportHandler",
    "MonitorHandler",
    "Phase",
    "SendMessageHandler",
    "StepUpHandler",
    "applied_rules",
    "build_action_handlers",
]
