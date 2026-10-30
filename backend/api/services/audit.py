"""The audit log: one row per attempt, whatever the attempt came to.

NFR-7 asks for a record of every action attempt — executed, denied, failed and
replayed. The emphasis is on the three that are not successes: a log holding
only what worked cannot answer "did anyone try to block this card?", which is
the first question an auditor asks and the only one a demo of a permission
boundary makes interesting.

Two fields are written from the server's own recomputation rather than from
the request: ``route`` and ``simulated``. A route echoed from the client would
turn the log into a transcript of what was claimed, and ``simulated`` is the
line between the thirteen actions that pretend and the one that really writes
to the graph.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC
from typing import Any, Literal

from sqlalchemy import func, select

from api.principal import Principal
from api.store.models import AuditRow, new_id
from api.store.session import Database
from sentinel.domain.enums import Action, Route

#: The four ways an attempt can end. The store's ``outcome`` columns hold these
#: strings and nothing else; the frontend's filter chips mirror them.
Outcome = Literal["executed", "denied", "failed", "replayed"]

OUTCOMES: tuple[Outcome, ...] = ("executed", "denied", "failed", "replayed")


@dataclass(frozen=True, slots=True)
class AuditPage:
    """One page of the log, with the total behind it.

    ``total`` is the count *before* the limit, because the console's pager
    needs to know there are 340 rows while showing 50 of them.
    """

    items: tuple[AuditRow, ...]
    total: int
    limit: int
    offset: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": [_row_as_dict(row) for row in self.items],
            "total": self.total,
            "limit": self.limit,
            "offset": self.offset,
        }


class AuditService:
    """Appends to the audit log and reads it back, newest first.

    Responsibility: one table, two operations. It decides nothing — the caller
    has already recomputed the route and knows how the attempt ended.
    Collaborators: ``Database`` for the session; ``ActionExecutionService`` and
    ``ApprovalService``, the only two writers.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def write(
        self,
        *,
        principal: Principal,
        case_id: str,
        action: Action,
        route: Route,
        outcome: Outcome,
        payload: Mapping[str, Any],
        result: Mapping[str, Any],
        simulated: bool,
        run_id: str | None = None,
        approval_ref: str | None = None,
        idempotency_key: str | None = None,
        request_id: str = "",
    ) -> AuditRow:
        """One row. Never raises for an ordinary write, so a failed action's
        audit cannot itself be the thing that fails."""
        row = AuditRow(
            audit_id=new_id("aud"),
            request_id=request_id,
            actor_id=principal.id,
            actor_role=principal.role.value,
            case_id=case_id,
            run_id=run_id,
            action=action.value,
            route=route.value,
            approval_ref=approval_ref,
            idempotency_key=idempotency_key,
            payload=dict(payload),
            result=dict(result),
            outcome=outcome,
            simulated=simulated,
        )
        async with self._db.session() as session:
            session.add(row)
        return row

    async def query(
        self,
        *,
        case_id: str | None = None,
        actor: str | None = None,
        action: Action | None = None,
        outcome: Outcome | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> AuditPage:
        """A page of the log, newest first, with every filter optional."""
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        criteria = []
        if case_id:
            criteria.append(AuditRow.case_id == case_id)
        if actor:
            criteria.append(AuditRow.actor_id == actor)
        if action is not None:
            criteria.append(AuditRow.action == action.value)
        if outcome is not None:
            criteria.append(AuditRow.outcome == outcome)

        async with self._db.session() as session:
            total = int(
                await session.scalar(select(func.count()).select_from(AuditRow).where(*criteria))
                or 0
            )
            rows = (
                await session.scalars(
                    select(AuditRow)
                    .where(*criteria)
                    # at, then audit_id: two rows written in the same
                    # millisecond still come back in a stable order, and the
                    # ULID prefix makes the id itself chronological.
                    .order_by(AuditRow.at.desc(), AuditRow.audit_id.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        return AuditPage(items=tuple(rows), total=total, limit=limit, offset=offset)


def audit_rows_as_dicts(rows: Sequence[AuditRow]) -> list[dict[str, Any]]:
    """The wire shape of a list of audit rows, for a controller to serialise."""
    return [_row_as_dict(row) for row in rows]


def _row_as_dict(row: AuditRow) -> dict[str, Any]:
    # SQLite returns a naive datetime even from a timezone-aware column, and a
    # timestamp with no Z is one the browser reads as local time.
    at = row.at if row.at.tzinfo else row.at.replace(tzinfo=UTC)
    return {
        "audit_id": row.audit_id,
        "at": at.isoformat().replace("+00:00", "Z"),
        "request_id": row.request_id,
        "actor_id": row.actor_id,
        "actor_role": row.actor_role,
        "case_id": row.case_id,
        "run_id": row.run_id,
        "action": row.action,
        "route": row.route,
        "approval_ref": row.approval_ref,
        "idempotency_key": row.idempotency_key,
        "payload": dict(row.payload or {}),
        "result": dict(row.result or {}),
        "outcome": row.outcome,
        "simulated": row.simulated,
    }
