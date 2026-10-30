"""The operational store: six tables SQLite holds and the graph should not.

A run's event journal, an execution record, an approval and an audit row are
*operational* state. None of them belongs in TigerGraph: they are not part of
the fraud picture, they change per request rather than per investigation, and
putting them in the graph would mean a judge reading the case subgraph wades
through idempotency keys.

Two invariants are enforced in the schema rather than in a service, because a
service can be bypassed and a constraint cannot:

- ``(run_id, seq)`` is unique on the journal, so replay by ``seq > ?`` can
  never return a duplicate or skip a gap.
- A partial unique index makes an approval idempotent on
  ``(case_id, action, phase)`` while it is pending, so the same denied action
  hit twice enqueues one approval and not two.

Ids are ULID-shaped: a sortable time prefix and a random tail, prefixed with
the kind. ``run_01J8…`` sorts chronologically in a directory listing and in a
``ORDER BY`` with no separate timestamp index.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: Crockford base32, the ULID alphabet. No I, L, O or U, so an id read aloud
#: over a demo is unambiguous.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        out.append(_ALPHABET[rem])
    return "".join(reversed(out))


def new_id(prefix: str) -> str:
    """``run_01J8F3…`` — sortable by time, unique by 80 random bits."""
    stamp = _encode(int(time.time() * 1000), 10)
    tail = _encode(int.from_bytes(os.urandom(10), "big"), 16)
    return f"{prefix}_{stamp}{tail}"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for every operational table."""


class Run(Base):
    """One investigation, live or replayed."""

    __tablename__ = "run"

    run_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(32), index=True)
    #: queued | running | completed | failed | cancelled
    status: Mapped[str] = mapped_column(String(16), index=True, default="queued")
    step: Mapped[int] = mapped_column(Integer, default=0)
    #: live | replay | scripted
    mode: Mapped[str] = mapped_column(String(16), default="live")
    orchestrator: Mapped[str] = mapped_column(String(48), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_s: Mapped[float] = mapped_column(Float, default=0.0)
    validation_ok: Mapped[bool | None] = mapped_column(Boolean, default=None)
    error_code: Mapped[str] = mapped_column(String(48), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    budget: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: Set when the run belongs to a `POST /api/benchmark/runs` batch.
    batch_id: Mapped[str | None] = mapped_column(String(40), index=True, default=None)


class RunEvent(Base):
    """One journaled SSE event.

    ``seq`` starts at 1 per run and is the SSE ``id:`` line, which is what makes
    ``Last-Event-ID`` replay a single indexed range scan.
    """

    __tablename__ = "run_event"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_run_event_seq"),
        Index("ix_run_event_replay", "run_id", "seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(40))
    seq: Mapped[int] = mapped_column(Integer)
    case_id: Mapped[str] = mapped_column(String(32), index=True)
    type: Mapped[str] = mapped_column(String(32))
    step: Mapped[int | None] = mapped_column(Integer, default=None)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ActionExecution(Base):
    """One attempt to execute an action — including the ones that were denied.

    Denials and failures are rows, not absences. An audit log that records only
    successes cannot answer "did anyone try to block this card?", which is the
    question an auditor actually asks.
    """

    __tablename__ = "action_execution"

    execution_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    case_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(String(40), default=None)
    action: Mapped[str] = mapped_column(String(32), index=True)
    #: initial | final — which phase of the recommendation this came from.
    phase: Mapped[str] = mapped_column(String(8), default="final")
    route: Mapped[str] = mapped_column(String(8))
    actor_id: Mapped[str] = mapped_column(String(48))
    actor_role: Mapped[str] = mapped_column(String(24))
    approval_id: Mapped[str | None] = mapped_column(String(40), default=None)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: executed | denied | failed | replayed
    outcome: Mapped[str] = mapped_column(String(16), index=True)
    #: False only for CREATE_CASE, which really does write to the graph.
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    request_id: Mapped[str] = mapped_column(String(40), default="")


class Approval(Base):
    """A pending or decided human approval for an L1/L2 action."""

    __tablename__ = "approval"
    __table_args__ = (
        # Enqueue is idempotent while pending: the same denied action, hit
        # twice, must not fill the inbox with duplicates. Partial, so the
        # history of decided approvals on the same action is kept.
        Index(
            "uq_approval_pending",
            "case_id",
            "action",
            "phase",
            unique=True,
            sqlite_where=text("status = 'pending'"),
        ),
    )

    approval_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    case_id: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(32))
    phase: Mapped[str] = mapped_column(String(8), default="final")
    route: Mapped[str] = mapped_column(String(8))
    exposure_usd: Mapped[float] = mapped_column(Float, default=0.0)
    requested_by: Mapped[str] = mapped_column(String(48))
    requested_role: Mapped[str] = mapped_column(String(24))
    #: pending | approved | rejected
    status: Mapped[str] = mapped_column(String(16), index=True, default="pending")
    decided_by: Mapped[str | None] = mapped_column(String(48), default=None)
    decided_role: Mapped[str | None] = mapped_column(String(24), default=None)
    note: Mapped[str] = mapped_column(Text, default="")
    execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("action_execution.execution_id"), default=None
    )


class AuditRow(Base):
    """The complete record of everything anyone asked this system to do.

    ``route`` is the *recomputed* route, never the one the client sent. That is
    the difference between an audit log and a transcript.
    """

    __tablename__ = "audit"

    audit_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    request_id: Mapped[str] = mapped_column(String(40), default="")
    actor_id: Mapped[str] = mapped_column(String(48), index=True)
    actor_role: Mapped[str] = mapped_column(String(24))
    case_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(String(40), default=None)
    action: Mapped[str] = mapped_column(String(32), index=True)
    route: Mapped[str] = mapped_column(String(8))
    approval_ref: Mapped[str | None] = mapped_column(String(40), default=None)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: executed | denied | failed | replayed
    outcome: Mapped[str] = mapped_column(String(16), index=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)


class IdempotencyRecord(Base):
    """A stored response, so a retried mutation is not a second mutation."""

    __tablename__ = "idempotency"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    execution_id: Mapped[str | None] = mapped_column(String(40), default=None)
