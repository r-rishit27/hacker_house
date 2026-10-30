"""The permission boundary, as an HTTP surface.

Three endpoints, and the middle one is the whole point of the service. An
analyst presses "block this card"; the route is recomputed from the routing
table at *this* case's exposure; it comes back `L2`; the request is refused
with a 403 that names the roles who could approve it and carries the approval
it just enqueued. Nothing about that is advisory — there is no path from this
controller to a handler that skips the check, because
``ActionExecutionService`` is the only thing that can call one.

The 403 body is deliberately fat. A judge reading it should not have to make a
second request to learn who can say yes.
"""

from __future__ import annotations

from typing import Any, ClassVar

from fastapi import Query, Request

from api.controller import ApiController, route
from api.errors import not_found
from api.schemas import (
    ActionPlan,
    ActionRow,
    ExecuteRequest,
    ExecutionOutcome,
    ExecutionPage,
    ExecutionRecord,
    Phase,
)
from api.services.execution import ActionPlan as ServicePlan
from api.services.execution import ActionRequest
from sentinel.domain.enums import Action, Role


class ActionsController(ApiController):
    """Execute an action, or explain in full why this principal may not.

    Responsibility: translate HTTP into an ``ActionRequest`` and a
    ``Principal``, and shape what comes back. Every decision — the route, the
    permission, the approval, the audit row — belongs to
    ``ActionExecutionService``.
    Collaborators: that service, and nothing else.
    """

    prefix = "/actions"
    tags: ClassVar[list[str]] = ["actions"]

    def routes(self) -> list[dict[str, Any]]:
        return [
            route(
                "/execute",
                self.execute,
                methods=("POST",),
                summary="Execute an action. 403 with an approval when the route is not auto.",
                errors={
                    403: "The recomputed route needs an approval this role cannot give.",
                    404: "No such case, or no answer written for it yet.",
                    409: "Conflicts with an execution already recorded.",
                    422: "The action is not one of the fourteen.",
                },
            ),
            route(
                "/executions",
                self.executions,
                summary="Every attempt, including the denied and the failed ones.",
            ),
            route(
                "/executions/{execution_id}",
                self.execution,
                summary="One execution record.",
                errors={404: "No such execution."},
            ),
        ]

    async def execute(self, body: ExecuteRequest, request: Request) -> ExecutionRecord:
        """One action, for one principal, through the one door."""
        record = await self.container.executions.execute(
            ActionRequest(
                case_id=body.case_id,
                action=body.action,
                phase=body.phase.value,
                payload=dict(body.payload),
                on_denied=body.on_denied.value,
                idempotency_key=self.idempotency_key(request),
                request_id=self.request_id(request),
            ),
            self.principal(request),
        )
        return ExecutionRecord.model_validate(record)

    async def executions(
        self,
        case_id: str | None = None,
        outcome: ExecutionOutcome | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> ExecutionPage:
        rows = await self.container.executions.executions(
            case_id=case_id,
            outcome=outcome.value if outcome is not None else None,
            limit=limit,
        )
        items = [ExecutionRecord.model_validate(row) for row in rows]
        return ExecutionPage(items=items, total=len(items))

    async def execution(self, execution_id: str) -> ExecutionRecord:
        row = await self.container.executions.execution(execution_id)
        if row is None:
            raise not_found("execution", execution_id)
        return ExecutionRecord.model_validate(row)


def plan_body(plan: ServicePlan, role: Role) -> ActionPlan:
    """The service's plan as the wire model, split by phase.

    The service keeps both phases in one tuple because a row knows its own
    phase; the console renders them side by side as a diff, so they arrive
    already separated. ``role`` is echoed because ``may_execute`` means
    nothing without the role it was computed for, and the console has a
    switcher.
    """
    return ActionPlan(
        case_id=plan.case_id,
        exposure_usd=plan.exposure_usd,
        role=role,
        what_changed=plan.what_changed,
        initial=[_row(r) for r in plan.rows if str(r.phase) == "initial"],
        final=[_row(r) for r in plan.rows if str(r.phase) == "final"],
    )


def _row(planned: Any) -> ActionRow:
    return ActionRow(
        action=Action(planned.action),
        route=planned.route,
        reason=planned.reason,
        answer_route=planned.stated_route,
        route_changed=planned.route_drifted,
        approvers=list(planned.approvers),
        may_execute=planned.may_execute,
    )


#: Re-exported so ``Phase`` is importable from one place by the controllers
#: that take it as a query parameter.
__all__ = ["ActionsController", "Phase", "plan_body"]
