"""The single door to every side effect, and the only thing allowed to open it.

Every action in this system — clicked in the console, curled by a judge, or
released by an approval — goes through :class:`ActionExecutionService`. Nothing
else may call an ``ActionHandler``. That rule is what makes the audit log
complete rather than flattering: a second entry point would not write rows for
the denials, and a permission boundary whose refusals leave no trace is a
boundary nobody can check afterwards.

Three things this module refuses to take from the request, all for the same
reason — a client must not be able to widen its own permissions:

- **the exposure**, which is read from the case's answer file;
- **the route**, which is recomputed from the routing table on every call
  (ADR-6), never echoed from the request or from the answer's stated route;
- **the approval**, which only :class:`ApprovalService` can produce.

A denial is not a dead end. Unless the caller opts out with
``on_denied="reject"``, the 403 idempotently enqueues the approval it needs and
carries the approval id in the problem body, so the console can go from
"blocked" to "in the inbox" without a second request.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import ValidationError
from sqlalchemy import select

from api.errors import ApiError, PermissionDenied, conflict
from api.handlers.actions import (
    ActionHandlerRegistry,
    ExecutionContext,
    Phase,
)
from api.principal import Principal
from api.services.audit import AuditService, Outcome
from api.services.idempotency import IdempotencyService
from api.services.permissions import RoutePermissionPolicy
from api.store.models import ActionExecution, Approval, IdempotencyRecord, new_id, utcnow
from api.store.session import Database
from sentinel.domain.alert import Alert, CasePackLoader
from sentinel.domain.answer import ActionRecommendation, AnswerFile
from sentinel.domain.enums import Action, Role, Route
from sentinel.domain.errors import SentinelError

logger = logging.getLogger(__name__)

#: What to do when the recomputed route is not ``auto``. The default enqueues
#: an approval; ``reject`` is for a caller that only wants to know.
#: What to do when the route needs an approval this principal cannot give.
#: The spelling is the wire's, from `api.schemas.DenialPolicy` — "enqueue"
#: because that is what happens, and because a service and a schema that
#: disagree about the value of a field produce a 403 with no approval in it
#: and no error anywhere.
DenialPolicy = Literal["enqueue", "reject"]

#: The idempotency namespace for ``POST /api/actions/execute``. Scoping keys by
#: endpoint means a console that reuses "block-HHG-017" for a different call
#: collides loudly rather than replaying the wrong stored response.
EXECUTE_SCOPE = "actions:execute"

#: A case id is a path segment that becomes a filename. Anything else is not a
#: case we have — and, unvalidated, would let a request walk out of cases/.
_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")

_PHASES: tuple[Phase, ...] = ("initial", "final")
_DENIAL_POLICIES: tuple[DenialPolicy, ...] = ("enqueue", "reject")


def parse_phase(value: str | None) -> Phase:
    """``"final"`` by default; anything but the two phases is a 422."""
    if value is None or value == "":
        return "final"
    if value in _PHASES:
        return value
    raise ApiError(
        f"phase '{value}' is not one of {', '.join(_PHASES)}",
        code="invalid_phase",
        status=422,
        phase=value,
    )


def parse_on_denied(value: str | None) -> DenialPolicy:
    """``"enqueue"`` by default — the behaviour the exit test depends on."""
    if value is None or value == "":
        return "enqueue"
    if value in _DENIAL_POLICIES:
        return value
    raise ApiError(
        f"on_denied '{value}' is not one of {', '.join(_DENIAL_POLICIES)}",
        code="invalid_on_denied",
        status=422,
        on_denied=value,
    )


def load_alerts(case_pack_path: Path) -> dict[str, Alert]:
    """The case pack, indexed by alert id, for the composition root.

    Twenty rows read once at start-up. The handlers need the customer and card
    ids, and the answer file deliberately does not carry them — it is the
    graded artefact and an extra key in it is a failure.
    """
    return {alert.alert_id: alert for alert in CasePackLoader(case_pack_path).load()}


class AnswerSource(Protocol):
    """Where a case's answer file comes from.

    A protocol rather than a class so a test, a fixture mode and the real
    ``cases/`` directory are interchangeable without any of them importing the
    others.
    """

    async def load(self, case_id: str) -> AnswerFile | None: ...


class FileAnswerSource:
    """Reads ``cases/<case_id>.json`` and parses it into the graded contract.

    Responsibility: one directory, one file per case, and the refusal to look
    anywhere else. Collaborators: ``AnswerFile``, which validates what it
    finds — a file that no longer satisfies the contract is reported as such
    rather than half-read.
    """

    def __init__(self, cases_dir: Path) -> None:
        self._dir = cases_dir

    async def load(self, case_id: str) -> AnswerFile | None:
        if not _CASE_ID.match(case_id):
            return None
        path = self._dir / f"{case_id}.json"
        # to_thread: this process also serves an SSE stream per open console
        # tab, and there is no reason to make those wait on a disk read.
        text = await asyncio.to_thread(_read_text, path)
        if text is None:
            return None
        try:
            return AnswerFile.model_validate_json(text)
        except ValidationError as exc:
            raise ApiError(
                f"the answer file for '{case_id}' no longer satisfies the answer contract",
                code="answer_unreadable",
                status=409,
                case_id=case_id,
                errors=exc.error_count(),
            ) from exc


def _read_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


async def require_answer(answers: AnswerSource, case_id: str) -> AnswerFile:
    """The case's answer file, or a 404 naming the case that has none.

    Every route recomputation starts here, because exposure comes from the
    answer and nowhere else. A case with no answer yet therefore has no
    executable actions — rather than an exposure of zero, which would quietly
    route ``BLOCK_CARD`` to L1 when it belongs at L2.
    """
    answer = await answers.load(case_id)
    if answer is None:
        raise ApiError(
            f"case '{case_id}' has no answer file yet, so it has no actions to execute",
            code="answer_not_written",
            status=404,
            case_id=case_id,
        )
    return answer


class ApprovalQueue(Protocol):
    """The half of ``ApprovalService`` this module needs.

    Declared here rather than imported so the dependency runs one way:
    approvals knows about execution, execution knows only this shape.
    """

    async def enqueue(
        self,
        *,
        case_id: str,
        action: Action,
        phase: Phase,
        route: Route,
        exposure_usd: float,
        principal: Principal,
    ) -> Approval: ...


@dataclass(frozen=True, slots=True)
class ActionRequest:
    """One resolved request to execute an action.

    Note what is *not* here: no route, no exposure and no approval reference.
    All three are the server's to determine, and a field for them would be a
    field a client could fill.
    """

    case_id: str
    action: Action
    phase: Phase = "final"
    payload: Mapping[str, Any] = field(default_factory=dict)
    on_denied: DenialPolicy = "enqueue"
    idempotency_key: str | None = None
    run_id: str | None = None
    request_id: str = ""

    def as_payload(self) -> dict[str, Any]:
        """The request as it is fingerprinted and audited."""
        return {
            "case_id": self.case_id,
            "action": self.action.value,
            "phase": self.phase,
            "payload": dict(self.payload),
            "on_denied": self.on_denied,
        }


@dataclass(frozen=True, slots=True)
class PlannedAction:
    """One row of the action plan, as the server sees it now.

    ``route`` is recomputed; ``stated_route`` is what the answer file claimed
    when it was written. They differ only if a threshold moved under a written
    answer, and the console badges that rather than hiding it (ADR-6).
    """

    phase: Phase
    action: Action
    route: Route
    stated_route: Route
    reason: str
    may_execute: bool
    approvers: tuple[Role, ...]

    @property
    def route_drifted(self) -> bool:
        return self.route is not self.stated_route

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "action": self.action.value,
            "route": self.route.value,
            "stated_route": self.stated_route.value,
            "route_drifted": self.route_drifted,
            "reason": self.reason,
            "may_execute": self.may_execute,
            "approvers": [role.value for role in self.approvers],
        }


@dataclass(frozen=True, slots=True)
class ActionPlan:
    """Both phases of one case's plan, routed for the asking principal."""

    case_id: str
    exposure_usd: float
    what_changed: str
    rows: tuple[PlannedAction, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "exposure_usd": self.exposure_usd,
            "what_changed": self.what_changed,
            "initial": [r.as_dict() for r in self.rows if r.phase == "initial"],
            "final": [r.as_dict() for r in self.rows if r.phase == "final"],
        }


class ActionExecutionService:
    """Resolve, authorise, dispatch, record. In that order, every time.

    Responsibility: the whole life of one action attempt, including the
    attempts that do not happen. It owns no effect itself — the effect belongs
    to an ``ActionHandler`` — and it makes no fraud decision.
    Collaborators: ``RoutePermissionPolicy`` for the route and the 403;
    ``ActionHandlerRegistry`` for the effect; ``ApprovalQueue`` for what a
    denial becomes; ``IdempotencyService`` for retry safety; ``AuditService``,
    which gets a row whatever happens.
    """

    def __init__(
        self,
        *,
        db: Database,
        handlers: ActionHandlerRegistry,
        permissions: RoutePermissionPolicy,
        audit: AuditService,
        idempotency: IdempotencyService,
        approvals: ApprovalQueue,
        answers: AnswerSource,
        alerts: Mapping[str, Alert],
    ) -> None:
        self._db = db
        self._handlers = handlers
        self._permissions = permissions
        self._audit = audit
        self._idempotency = idempotency
        self._approvals = approvals
        self._answers = answers
        self._alerts = alerts

    # ── the public door ──────────────────────────────────────────────────────

    async def execute(self, request: ActionRequest, principal: Principal) -> ActionExecution:
        """Execute one action for one principal, or explain why not.

        Raises ``PermissionDenied`` when the recomputed route is not ``auto`` —
        carrying the approval it just enqueued — and lets a handler's own
        failure propagate after recording it.
        """
        key = request.idempotency_key
        if key:
            stored = await self._idempotency.reserve(
                key, EXECUTE_SCOPE, IdempotencyService.fingerprint(request.as_payload())
            )
            if stored is not None:
                return await self._replay(stored, request, principal)

        finished = False
        try:
            execution = await self._execute_once(request, principal)
            finished = True
        finally:
            # A denial, a 404 or a handler failure releases the key: the client
            # may fix the cause and retry with the same one. Only a completed
            # execution is remembered, because only it must not happen twice.
            if key and not finished:
                await self._idempotency.release(key, EXECUTE_SCOPE)

        if key:
            await self._idempotency.complete(
                key,
                EXECUTE_SCOPE,
                status=200,
                body=execution_as_dict(execution),
                execution_id=execution.execution_id,
            )
        return execution

    async def execute_approved(
        self, approval: Approval, principal: Principal, *, request_id: str = ""
    ) -> ActionExecution:
        """Execute an action an approval has released.

        The permission check is deliberately absent: the approval *is* the
        permission, and ``ApprovalService`` re-asserted the route and the
        approver's role a moment before calling this. The actor recorded is the
        approver, because they are who caused the effect.
        """
        answer = await self.answer_for(approval.case_id)
        exposure = answer.case.exposure_usd
        action = Action(approval.action)
        route = self._permissions.route_of(action, exposure)
        request = ActionRequest(
            case_id=approval.case_id,
            action=action,
            phase=parse_phase(approval.phase),
            on_denied="reject",
            request_id=request_id,
        )
        return await self._dispatch(
            request,
            principal,
            route=route,
            exposure_usd=exposure,
            answer=answer,
            approval_ref=approval.approval_id,
        )

    # ── reads ────────────────────────────────────────────────────────────────

    async def answer_for(self, case_id: str) -> AnswerFile:
        """The case's answer file, or a 404 that says which one is missing."""
        return await require_answer(self._answers, case_id)

    async def plan(
        self, case_id: str, principal: Principal, phase: Phase | None = None
    ) -> ActionPlan:
        """The case's action plan, with every route recomputed for this principal."""
        answer = await self.answer_for(case_id)
        exposure = answer.case.exposure_usd
        rows: list[PlannedAction] = []
        phases: tuple[Phase, ...] = (phase,) if phase is not None else _PHASES
        for one in phases:
            recommendations: Sequence[ActionRecommendation] = (
                answer.next_best_actions.initial
                if one == "initial"
                else answer.next_best_actions.final
            )
            rows.extend(self._planned(one, rec, principal, exposure) for rec in recommendations)
        return ActionPlan(
            case_id=case_id,
            exposure_usd=exposure,
            what_changed=answer.next_best_actions.what_changed,
            rows=tuple(rows),
        )

    async def executions(
        self,
        *,
        case_id: str | None = None,
        outcome: Outcome | None = None,
        limit: int = 50,
    ) -> tuple[ActionExecution, ...]:
        """Recent attempts, newest first — denials and failures included."""
        limit = max(1, min(limit, 500))
        criteria = []
        if case_id:
            criteria.append(ActionExecution.case_id == case_id)
        if outcome is not None:
            criteria.append(ActionExecution.outcome == outcome)
        async with self._db.session() as session:
            rows = (
                await session.scalars(
                    select(ActionExecution)
                    .where(*criteria)
                    .order_by(ActionExecution.at.desc(), ActionExecution.execution_id.desc())
                    .limit(limit)
                )
            ).all()
        return tuple(rows)

    async def execution(self, execution_id: str) -> ActionExecution | None:
        async with self._db.session() as session:
            return await session.get(ActionExecution, execution_id)

    # ── the path one attempt takes ───────────────────────────────────────────

    async def _execute_once(self, request: ActionRequest, principal: Principal) -> ActionExecution:
        answer = await self.answer_for(request.case_id)
        exposure = answer.case.exposure_usd
        route = self._permissions.route_of(request.action, exposure)
        try:
            self._permissions.assert_may_execute(principal, request.action, exposure)
        except PermissionDenied as denied:
            await self._deny(request, principal, route, exposure, denied)
            raise
        return await self._dispatch(
            request,
            principal,
            route=route,
            exposure_usd=exposure,
            answer=answer,
            approval_ref=None,
        )

    async def _dispatch(
        self,
        request: ActionRequest,
        principal: Principal,
        *,
        route: Route,
        exposure_usd: float,
        answer: AnswerFile,
        approval_ref: str | None,
    ) -> ActionExecution:
        handler = self._handlers.for_action(request.action)
        ctx = ExecutionContext(
            case_id=request.case_id,
            action=request.action,
            phase=request.phase,
            route=route,
            exposure_usd=exposure_usd,
            principal=principal,
            answer=answer,
            payload=dict(request.payload),
            alert=self._alerts.get(request.case_id),
            approval_ref=approval_ref,
            request_id=request.request_id,
            run_id=request.run_id,
            at=utcnow(),
        )
        try:
            outcome = await handler.execute(ctx)
        except SentinelError as exc:
            await self._record(
                request,
                principal,
                route=route,
                exposure_usd=exposure_usd,
                outcome="failed",
                result={"code": exc.code, "message": exc.message},
                simulated=True,
                approval_ref=approval_ref,
            )
            raise
        return await self._record(
            request,
            principal,
            route=route,
            exposure_usd=exposure_usd,
            outcome="executed",
            result=outcome.as_result(),
            simulated=outcome.simulated,
            approval_ref=approval_ref,
        )

    async def _deny(
        self,
        request: ActionRequest,
        principal: Principal,
        route: Route,
        exposure_usd: float,
        denied: PermissionDenied,
    ) -> None:
        """Turn a refusal into an approval, a row and a problem body.

        The approval is attached to the exception the caller is about to see,
        so the console's 403 handler can link straight to the inbox — the one
        unusual piece of REST in this API, and the reason the exit test is a
        single click.
        """
        approval: Approval | None = None
        if request.on_denied == "enqueue":
            approval = await self._approvals.enqueue(
                case_id=request.case_id,
                action=request.action,
                phase=request.phase,
                route=route,
                exposure_usd=exposure_usd,
                principal=principal,
            )
            denied.context["approval"] = {
                "approval_id": approval.approval_id,
                "status": approval.status,
                "href": f"/api/approvals/{approval.approval_id}",
            }
        await self._record(
            request,
            principal,
            route=route,
            exposure_usd=exposure_usd,
            outcome="denied",
            result={
                "reason": denied.message,
                "required_route": route.value,
                "roles_that_can_approve": [
                    role.value for role in self._permissions.approvers_for(route)
                ],
                "approval_id": approval.approval_id if approval else None,
            },
            simulated=True,
            approval_ref=approval.approval_id if approval else None,
        )
        logger.info(
            "denied %s on %s for %s: route %s",
            request.action.value,
            request.case_id,
            principal.id,
            route.value,
        )

    async def _replay(
        self, stored: IdempotencyRecord, request: ActionRequest, principal: Principal
    ) -> ActionExecution:
        """Return the first execution again, and record that we were asked twice.

        The replay row is what distinguishes "the analyst clicked once" from
        "the analyst clicked four times and we blocked one card" — both are
        answerable from the log, which is the point of recording the retry at
        all.
        """
        execution = await self.execution(stored.execution_id) if stored.execution_id else None
        if execution is None:
            raise conflict(
                f"idempotency key '{request.idempotency_key}' has a stored response but "
                "no execution behind it",
                code="idempotency_orphaned",
                key=request.idempotency_key,
            )
        await self._audit.write(
            principal=principal,
            case_id=execution.case_id,
            action=Action(execution.action),
            route=Route(execution.route),
            outcome="replayed",
            payload=request.as_payload(),
            result={"execution_id": execution.execution_id, "replay_of": execution.outcome},
            simulated=execution.simulated,
            run_id=execution.run_id,
            approval_ref=execution.approval_id,
            idempotency_key=request.idempotency_key,
            request_id=request.request_id,
        )
        return execution

    async def _record(
        self,
        request: ActionRequest,
        principal: Principal,
        *,
        route: Route,
        exposure_usd: float,
        outcome: Outcome,
        result: Mapping[str, Any],
        simulated: bool,
        approval_ref: str | None,
    ) -> ActionExecution:
        """One execution row and one audit row, for every outcome there is."""
        execution = ActionExecution(
            execution_id=new_id("exe"),
            at=utcnow(),
            case_id=request.case_id,
            run_id=request.run_id,
            action=request.action.value,
            phase=request.phase,
            route=route.value,
            actor_id=principal.id,
            actor_role=principal.role.value,
            approval_id=approval_ref,
            idempotency_key=request.idempotency_key,
            payload=dict(request.payload),
            result=dict(result),
            outcome=outcome,
            simulated=simulated,
            request_id=request.request_id,
        )
        async with self._db.session() as session:
            session.add(execution)
        await self._audit.write(
            principal=principal,
            case_id=request.case_id,
            action=request.action,
            route=route,
            outcome=outcome,
            payload={**request.as_payload(), "exposure_usd": exposure_usd},
            result=dict(result),
            simulated=simulated,
            run_id=request.run_id,
            approval_ref=approval_ref,
            idempotency_key=request.idempotency_key,
            request_id=request.request_id,
        )
        return execution

    def _planned(
        self,
        phase: Phase,
        recommendation: ActionRecommendation,
        principal: Principal,
        exposure_usd: float,
    ) -> PlannedAction:
        route = self._permissions.route_of(recommendation.action, exposure_usd)
        return PlannedAction(
            phase=phase,
            action=recommendation.action,
            route=route,
            stated_route=recommendation.route,
            reason=recommendation.reason,
            may_execute=self._permissions.may_execute(
                principal, recommendation.action, exposure_usd
            ),
            approvers=self._permissions.approvers_for(route),
        )


def execution_as_dict(execution: ActionExecution) -> dict[str, Any]:
    """The wire shape of one execution record."""
    # SQLite hands back a naive datetime even for a timezone-aware column, and
    # a timestamp without a Z is a timestamp the browser reads as local.
    at = execution.at if execution.at.tzinfo else execution.at.replace(tzinfo=UTC)
    return {
        "execution_id": execution.execution_id,
        "at": at.isoformat().replace("+00:00", "Z"),
        "case_id": execution.case_id,
        "run_id": execution.run_id,
        "action": execution.action,
        "phase": execution.phase,
        "route": execution.route,
        "actor_id": execution.actor_id,
        "actor_role": execution.actor_role,
        "approval_id": execution.approval_id,
        "idempotency_key": execution.idempotency_key,
        "payload": dict(execution.payload or {}),
        "result": dict(execution.result or {}),
        "outcome": execution.outcome,
        "simulated": execution.simulated,
        "request_id": execution.request_id,
    }
