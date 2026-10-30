"""The approval inbox: pending, decided, and re-checked at the last moment.

An approval is created by a refusal. ``ActionExecutionService`` denies an
analyst's ``BLOCK_CARD``, enqueues the approval that would release it, and
hands the id back inside the 403; a fraud manager then decides it here. That
round trip is the demo's exit test, and it is also the only place in the
system where one person's authority stands behind another's action.

**The route is re-asserted at decision time, not trusted from the request.**
The approval row records the route the denial computed, but by the time a
human opens the inbox the case may have been re-investigated and the exposure
may have moved — $2,480 to $2,680 turns an L1 into an L2. Recomputing at the
decision means a team lead cannot approve, at their leisure, something that
has since become a fraud manager's call. Without that check the approval queue
would be a way to launder an escalation past the boundary that created it.

Enqueueing is idempotent while an approval is pending, enforced by a partial
unique index on ``(case_id, action, phase)`` rather than by this service: a
service can be bypassed and a constraint cannot. Clicking a blocked button
four times fills the inbox with one row.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from api.errors import ApiError, conflict, not_found
from api.handlers.actions import Phase
from api.principal import Principal
from api.services.audit import AuditService
from api.services.execution import AnswerSource, execution_as_dict, require_answer
from api.services.permissions import RoutePermissionPolicy
from api.store.models import ActionExecution, Approval, new_id, utcnow
from api.store.session import Database
from sentinel.domain.enums import Action, Route

logger = logging.getLogger(__name__)

#: What a human can do with a pending approval.
Decision = Literal["approve", "reject"]

_DECISIONS: tuple[Decision, ...] = ("approve", "reject")

#: The three states an approval row can be in.
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"


def parse_decision(value: str | None) -> Decision:
    """``approve`` or ``reject``; anything else is a 422, not a guess."""
    if value in _DECISIONS:
        return value
    raise ApiError(
        f"decision '{value}' is not one of {', '.join(_DECISIONS)}",
        code="invalid_decision",
        status=422,
        decision=value,
    )


class ApprovalExecutor(Protocol):
    """The half of ``ActionExecutionService`` an approval needs.

    A protocol, so the object graph's one cycle — a denial makes an approval,
    an approval makes an execution — is closed by :meth:`ApprovalService.bind`
    at wiring time rather than by an import cycle between the two modules.
    """

    async def execute_approved(
        self, approval: Approval, principal: Principal, *, request_id: str = ""
    ) -> ActionExecution: ...


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """What a decision produced: the decided approval, and any execution.

    ``execution`` is ``None`` for a rejection, and for a rejection only — an
    approval that produced no execution is a bug, not a state.
    """

    approval: Approval
    execution: ActionExecution | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "approval": approval_as_dict(self.approval),
            "execution": execution_as_dict(self.execution) if self.execution else None,
        }


@dataclass(frozen=True, slots=True)
class ApprovalPage:
    """The inbox: the rows asked for, and the counts the badge needs."""

    items: tuple[Approval, ...]
    counts: Mapping[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": [approval_as_dict(a) for a in self.items],
            "counts": dict(self.counts),
        }


class ApprovalService:
    """The pending -> approved -> executed lifecycle of one authorisation.

    Responsibility: the approval row and the decision, including the route
    re-assertion that makes the decision meaningful. It executes nothing
    itself — the bound executor is the single door to every side effect.
    Collaborators: ``RoutePermissionPolicy`` for the recomputed route and its
    approvers; ``AnswerSource`` for the exposure that determines it;
    ``AuditService`` for the rejection row; ``ApprovalExecutor`` for the act
    an approval releases.
    """

    def __init__(
        self,
        *,
        db: Database,
        permissions: RoutePermissionPolicy,
        answers: AnswerSource,
        audit: AuditService,
    ) -> None:
        self._db = db
        self._permissions = permissions
        self._answers = answers
        self._audit = audit
        self._executor: ApprovalExecutor | None = None

    def bind(self, executor: ApprovalExecutor) -> None:
        """Close the one cycle in the object graph, at wiring time.

        The container builds this service first, because the execution service
        needs it to enqueue on a denial, and hands the execution service back
        here afterwards. A setter rather than a constructor argument because
        the dependency genuinely is mutual; hiding it behind a lazy import
        would make it no less mutual and considerably less obvious.
        """
        self._executor = executor

    # ── the inbox ────────────────────────────────────────────────────────────

    async def enqueue(
        self,
        *,
        case_id: str,
        action: Action,
        phase: Phase,
        route: Route,
        exposure_usd: float,
        principal: Principal,
    ) -> Approval:
        """The pending approval for this action, creating it if there is none.

        Idempotent on ``(case_id, action, phase)`` while pending, so a denied
        button pressed repeatedly yields one row and one id.
        """
        existing = await self._pending(case_id, action, phase)
        if existing is not None:
            return existing
        approval = Approval(
            approval_id=new_id("apr"),
            case_id=case_id,
            action=action.value,
            phase=phase,
            route=route.value,
            exposure_usd=exposure_usd,
            requested_by=principal.id,
            requested_role=principal.role.value,
            status=PENDING,
        )
        try:
            async with self._db.session() as session:
                session.add(approval)
        except IntegrityError:
            # Two denials of the same action raced. The partial unique index
            # settled it; whichever row won is the one both callers get.
            winner = await self._pending(case_id, action, phase)
            if winner is None:
                raise
            return winner
        logger.info(
            "approval %s queued: %s on %s needs %s",
            approval.approval_id,
            action.value,
            case_id,
            route.value,
        )
        return approval

    async def get(self, approval_id: str) -> Approval:
        async with self._db.session() as session:
            approval = await session.get(Approval, approval_id)
        if approval is None:
            raise not_found("approval", approval_id)
        return approval

    async def list(
        self,
        *,
        status: str | None = None,
        route: Route | None = None,
        case_id: str | None = None,
        limit: int = 100,
    ) -> ApprovalPage:
        """The inbox, oldest first — an approval queue is a work queue."""
        limit = max(1, min(limit, 500))
        criteria = []
        if status:
            criteria.append(Approval.status == status)
        if route is not None:
            criteria.append(Approval.route == route.value)
        if case_id:
            criteria.append(Approval.case_id == case_id)
        async with self._db.session() as session:
            rows = (
                await session.scalars(
                    select(Approval)
                    .where(*criteria)
                    .order_by(Approval.created_at, Approval.approval_id)
                    .limit(limit)
                )
            ).all()
            # Counts are over the whole table, not the filtered page: the
            # sidebar badge says how much work exists, not how much is shown.
            tallies = (
                await session.execute(
                    select(Approval.status, func.count()).group_by(Approval.status)
                )
            ).all()
        counts = {state: 0 for state in (PENDING, APPROVED, REJECTED)}
        for state, count in tallies:
            counts[str(state)] = int(count)
        return ApprovalPage(items=tuple(rows), counts=counts)

    # ── the decision ─────────────────────────────────────────────────────────

    async def decide(
        self,
        approval_id: str,
        decision: Decision,
        principal: Principal,
        *,
        note: str = "",
        request_id: str = "",
    ) -> ApprovalDecision:
        """Approve or reject, with the route re-asserted against today's exposure."""
        approval = await self.get(approval_id)
        if approval.status != PENDING:
            raise conflict(
                f"approval '{approval_id}' was already {approval.status}",
                code="approval_already_decided",
                approval_id=approval_id,
                # Not "status": that key is the HTTP status in a problem body.
                approval_status=approval.status,
            )

        action = Action(approval.action)
        answer = await require_answer(self._answers, approval.case_id)
        exposure = answer.case.exposure_usd
        route = self._permissions.route_of(action, exposure)
        self._assert_may_approve(approval, principal, route, exposure)

        approved = decision == "approve"
        approval = await self._settle(
            approval_id,
            status=APPROVED if approved else REJECTED,
            route=route,
            exposure_usd=exposure,
            principal=principal,
            note=note,
        )
        if not approved:
            # A rejection produces no execution, so this is the only row that
            # will ever record it. Without it the log would show the request
            # and never its refusal.
            await self._audit.write(
                principal=principal,
                case_id=approval.case_id,
                action=action,
                route=route,
                outcome="denied",
                payload={
                    "approval_id": approval_id,
                    "phase": approval.phase,
                    "exposure_usd": exposure,
                    "decision": decision,
                },
                result={"note": note, "requested_by": approval.requested_by},
                simulated=True,
                approval_ref=approval_id,
                request_id=request_id,
            )
            return ApprovalDecision(approval=approval, execution=None)

        executor = self._executor
        if executor is None:
            raise RuntimeError(
                "ApprovalService has no executor bound; the container must call "
                "bind(action_execution_service) after constructing both"
            )
        # Marked approved before the effect runs, so a handler that fails
        # leaves an approved approval with no execution — recoverable, and
        # visible — rather than an effect nobody authorised.
        execution = await executor.execute_approved(approval, principal, request_id=request_id)
        approval = await self._attach(approval_id, execution.execution_id)
        logger.info(
            "approval %s approved by %s; execution %s",
            approval_id,
            principal.id,
            execution.execution_id,
        )
        return ApprovalDecision(approval=approval, execution=execution)

    # ── internals ────────────────────────────────────────────────────────────

    def _assert_may_approve(
        self, approval: Approval, principal: Principal, route: Route, exposure_usd: float
    ) -> None:
        """The check that makes an approval mean something."""
        if principal.may_approve(route):
            return
        approvers = self._permissions.approvers_for(route)
        raise ApiError(
            f"{approval.action} routes to {route.value} at exposure ${exposure_usd:,.2f}. "
            f"Role '{principal.role.value}' may not approve it.",
            code="role_insufficient_for_approval",
            status=403,
            approval_id=approval.approval_id,
            action=approval.action,
            required_route=route.value,
            your_role=principal.role.value,
            roles_that_can_approve=[role.value for role in approvers],
        )

    async def _pending(self, case_id: str, action: Action, phase: Phase) -> Approval | None:
        async with self._db.session() as session:
            pending: Approval | None = await session.scalar(
                select(Approval).where(
                    Approval.case_id == case_id,
                    Approval.action == action.value,
                    Approval.phase == phase,
                    Approval.status == PENDING,
                )
            )
        return pending

    async def _settle(
        self,
        approval_id: str,
        *,
        status: str,
        route: Route,
        exposure_usd: float,
        principal: Principal,
        note: str,
    ) -> Approval:
        async with self._db.session() as session:
            approval = await session.get(Approval, approval_id)
            if approval is None:  # deleted between the read and the write
                raise not_found("approval", approval_id)
            approval.status = status
            approval.decided_at = utcnow()
            approval.decided_by = principal.id
            approval.decided_role = principal.role.value
            approval.note = note
            # The recomputed route replaces the one the denial recorded, so the
            # row says what was actually approved rather than what was asked.
            approval.route = route.value
            approval.exposure_usd = exposure_usd
            return approval

    async def _attach(self, approval_id: str, execution_id: str) -> Approval:
        async with self._db.session() as session:
            approval = await session.get(Approval, approval_id)
            if approval is None:
                raise not_found("approval", approval_id)
            approval.execution_id = execution_id
            return approval


def approval_as_dict(approval: Approval) -> dict[str, Any]:
    """The wire shape of one approval."""
    return {
        "approval_id": approval.approval_id,
        "created_at": _stamp(approval.created_at),
        "decided_at": _stamp(approval.decided_at),
        "case_id": approval.case_id,
        "action": approval.action,
        "phase": approval.phase,
        "route": approval.route,
        "exposure_usd": approval.exposure_usd,
        "requested_by": approval.requested_by,
        "requested_role": approval.requested_role,
        "status": approval.status,
        "decided_by": approval.decided_by,
        "decided_role": approval.decided_role,
        "note": approval.note,
        "execution_id": approval.execution_id,
        "href": f"/api/approvals/{approval.approval_id}",
    }


def _stamp(moment: datetime | None) -> str | None:
    """ISO-8601 with a Z. SQLite hands back naive datetimes from an aware
    column, and a timestamp with no zone is one the browser reads as local."""
    if moment is None:
        return None
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return aware.isoformat().replace("+00:00", "Z")
