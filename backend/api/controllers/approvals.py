"""The other half of the permission boundary: the human who says yes.

A denial enqueues an approval; this is where someone with the right role
decides it. Two things here are not obvious and both are deliberate.

**The route is re-asserted at decision time.** The approval records the route
it was enqueued at, and this controller does not trust it. A team lead
deciding an approval that is now `L2` — because the exposure moved, or because
the row was stale — gets a 403 naming the roles who can. Checking only at
enqueue would make the approval a token that outlives the fact it was issued
against.

**Approving executes.** The approval is the permission, so the service calls
the handler with the approval reference attached and the audit row records the
approver as the actor. An approval that only marked a row approved would leave
the question of who caused the effect unanswered.
"""

from __future__ import annotations

from typing import Any, ClassVar

from fastapi import Query, Request

from api.controller import ApiController, route
from api.schemas import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalDecisionResult,
    ApprovalItem,
    ApprovalPage,
    ApprovalStatus,
    AuditItem,
    AuditPage,
    ExecutionRecord,
)
from sentinel.domain.enums import Action, Route


class ApprovalsController(ApiController):
    """The approvals inbox, and the decision that releases an action.

    Responsibility: HTTP shape only. ``ApprovalService`` owns the re-assertion
    and the execution; ``AuditService`` owns the record.
    """

    prefix = "/approvals"
    tags: ClassVar[list[str]] = ["approvals"]

    def routes(self) -> list[dict[str, Any]]:
        return [
            route("", self.list_approvals, summary="The inbox, with counts by status."),
            route(
                "/{approval_id}",
                self.get_approval,
                summary="One approval.",
                errors={404: "No such approval."},
            ),
            route(
                "/{approval_id}/decision",
                self.decide,
                methods=("POST",),
                summary="Approve or reject. The route is re-asserted here, not trusted.",
                errors={
                    403: "This role cannot approve at the route the action carries now.",
                    404: "No such approval.",
                    409: "Already decided.",
                },
            ),
            route(
                "/{approval_id}/approve",
                self.approve,
                methods=("POST",),
                summary='Alias for a decision of "approve".',
                errors={403: "This role cannot approve at this route.", 404: "No such approval."},
            ),
        ]

    async def list_approvals(
        self,
        status: ApprovalStatus | None = None,
        route_filter: Route | None = Query(default=None, alias="route"),
        case_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> ApprovalPage:
        page = await self.container.approvals.list(
            status=status.value if status is not None else None,
            route=route_filter,
            case_id=case_id,
            limit=limit,
        )
        return _approvals(page)

    async def get_approval(self, approval_id: str) -> ApprovalItem:
        return ApprovalItem.model_validate(await self.container.approvals.get(approval_id))

    async def decide(
        self, approval_id: str, body: ApprovalDecisionRequest, request: Request
    ) -> ApprovalDecisionResult:
        decision = await self.container.approvals.decide(
            approval_id,
            body.decision.value,
            self.principal(request),
            note=body.note,
            request_id=self.request_id(request),
        )
        return ApprovalDecisionResult(
            approval=ApprovalItem.model_validate(decision.approval),
            execution=(
                ExecutionRecord.model_validate(decision.execution)
                if decision.execution is not None
                else None
            ),
        )

    async def approve(self, approval_id: str, request: Request) -> ApprovalDecisionResult:
        """The one-click path. Identical to a decision of "approve"."""
        return await self.decide(
            approval_id,
            ApprovalDecisionRequest(decision=ApprovalDecision.APPROVE, note=""),
            request,
        )


class AuditController(ApiController):
    """Everything anyone asked this system to do, successful or not.

    Separate from the approvals controller only because ``/api/audit`` is not
    under ``/api/approvals``. An audit log that recorded only what succeeded
    could not answer "did anyone try to block this card?", which is the
    question an auditor actually asks, so denials and failures are rows here
    too.
    """

    prefix = "/audit"
    tags: ClassVar[list[str]] = ["audit"]

    def routes(self) -> list[dict[str, Any]]:
        return [route("", self.list_audit, summary="The audit trail, newest first.")]

    async def list_audit(
        self,
        case_id: str | None = None,
        actor: str | None = None,
        action: Action | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> AuditPage:
        page = await self.container.audit.query(
            case_id=case_id, actor=actor, action=action, limit=limit, offset=offset
        )
        return _audit(page)


def _approvals(page: Any) -> ApprovalPage:
    """A service page of approvals as the wire page.

    The service carries per-status counts for the inbox badge; the wire page
    carries a total. The total is the sum, not a separate query, so the badge
    and the list cannot disagree.
    """
    return ApprovalPage(
        items=[ApprovalItem.model_validate(row) for row in page.items],
        total=sum(page.counts.values()),
    )


def _audit(page: Any) -> AuditPage:
    """A service page of audit rows as the wire page."""
    return AuditPage(items=[AuditItem.model_validate(row) for row in page.items], total=page.total)
