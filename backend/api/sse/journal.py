"""The event journal and the broker that fans it out.

One ordering invariant carries the whole design: **the journal row is written
before the event is published.** A subscriber therefore can never be ahead of
the journal, so a client that drops and reconnects with ``Last-Event-ID: 41``
gets exactly what it missed — no gap, and no duplicate. Publishing first and
journaling after would make that guarantee a coin toss.

``seq`` is per run, starts at 1, and is the SSE ``id:`` line. That is why
replay is a single indexed range scan rather than a timestamp comparison with
a tie-break.

Keepalives are comments. They are not journaled, not delivered as events, and
do not advance ``seq`` — a keepalive that took an id would make the next real
event look like a gap to a reconnecting client.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from api.store.models import RunEvent
from api.store.session import Database

logger = logging.getLogger(__name__)

#: The envelope version. Bumped only when a field is removed or retyped.
ENVELOPE_VERSION = 1


@dataclass(frozen=True, slots=True)
class SseEnvelope:
    """One event as it goes on the wire."""

    v: int
    seq: int
    run_id: str
    case_id: str
    type: str
    at: str
    step: int | None
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "v": self.v,
            "seq": self.seq,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "type": self.type,
            "at": self.at,
            "step": self.step,
            "payload": self.payload,
        }


class EventJournal:
    """Durable, ordered storage for one run's events.

    Responsibility: assign ``seq``, persist, and read back a range. It does not
    publish — that is the broker's job, and keeping them apart is what makes
    the write-then-publish order explicit at the call site rather than implied.
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        # seq is assigned in Python rather than by the database so the envelope
        # can carry it before the row is visible to a reader. The lock makes
        # that safe under the concurrent runs a benchmark batch allows.
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, run_id: str) -> asyncio.Lock:
        return self._locks.setdefault(run_id, asyncio.Lock())

    async def append(
        self,
        run_id: str,
        case_id: str,
        event_type: str,
        payload: dict[str, Any],
        step: int | None = None,
    ) -> SseEnvelope:
        """Persist one event and return its envelope, ``seq`` already assigned."""
        async with self._lock(run_id), self._db.session() as session:
            highest = await session.scalar(
                select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id)
            )
            seq = int(highest or 0) + 1
            at = datetime.now(UTC)
            session.add(
                RunEvent(
                    run_id=run_id,
                    seq=seq,
                    case_id=case_id,
                    type=event_type,
                    step=step,
                    at=at,
                    payload=payload,
                )
            )
        return SseEnvelope(
            v=ENVELOPE_VERSION,
            seq=seq,
            run_id=run_id,
            case_id=case_id,
            type=event_type,
            at=at.isoformat().replace("+00:00", "Z"),
            step=step,
            payload=payload,
        )

    async def since(self, run_id: str, after_seq: int = 0, limit: int = 5_000) -> list[SseEnvelope]:
        """Every event after ``after_seq``, in order. The replay query."""
        async with self._db.session() as session:
            rows = (
                await session.scalars(
                    select(RunEvent)
                    .where(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
                    .order_by(RunEvent.seq)
                    .limit(limit)
                )
            ).all()
        return [_envelope(row) for row in rows]

    async def count(self, run_id: str) -> int:
        async with self._db.session() as session:
            return int(
                await session.scalar(
                    select(func.count()).select_from(RunEvent).where(RunEvent.run_id == run_id)
                )
                or 0
            )

    def forget(self, run_id: str) -> None:
        """Drop the sequence lock for a finished run."""
        self._locks.pop(run_id, None)


def _envelope(row: RunEvent) -> SseEnvelope:
    at = row.at if row.at.tzinfo else row.at.replace(tzinfo=UTC)
    return SseEnvelope(
        v=ENVELOPE_VERSION,
        seq=row.seq,
        run_id=row.run_id,
        case_id=row.case_id,
        type=row.type,
        at=at.isoformat().replace("+00:00", "Z"),
        step=row.step,
        payload=dict(row.payload or {}),
    )


@dataclass(eq=False)
class Subscription:
    """One live listener on one run.

    ``eq=False`` so the dataclass keeps identity hashing: each subscription
    *is* one connection, two are never interchangeable, and the default
    ``eq=True`` sets ``__hash__`` to None — which makes the broker's set of
    subscribers a ``TypeError`` the moment anyone opens a stream.

    The queue is bounded. A subscriber that cannot keep up is dropped and told
    to reconnect rather than allowed to grow a queue without limit — a browser
    tab left open on a laptop lid is not a reason to hold a run's whole event
    history in memory.
    """

    run_id: str
    queue: asyncio.Queue[SseEnvelope | None]
    dropped: bool = False

    async def __aiter__(self) -> AsyncIterator[SseEnvelope]:
        while True:
            item = await self.queue.get()
            if item is None:
                return
            yield item


@dataclass
class SseBroker:
    """Fan-out from one run to every open connection on it."""

    queue_size: int = 256
    _subs: dict[str, set[Subscription]] = field(default_factory=dict)

    def subscribe(self, run_id: str) -> Subscription:
        sub = Subscription(run_id=run_id, queue=asyncio.Queue(maxsize=self.queue_size))
        self._subs.setdefault(run_id, set()).add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        listeners = self._subs.get(sub.run_id)
        if listeners is not None:
            listeners.discard(sub)
            if not listeners:
                self._subs.pop(sub.run_id, None)

    def publish(self, run_id: str, envelope: SseEnvelope) -> None:
        """Deliver to every live subscriber. Never blocks, never raises."""
        for sub in list(self._subs.get(run_id, ())):
            try:
                sub.queue.put_nowait(envelope)
            except asyncio.QueueFull:
                sub.dropped = True
                logger.warning(
                    "sse subscriber on %s fell %d events behind; dropping it",
                    run_id,
                    self.queue_size,
                )
                self._close(sub)

    def close(self, run_id: str) -> None:
        """End every connection on a finished run."""
        for sub in list(self._subs.get(run_id, ())):
            self._close(sub)
        self._subs.pop(run_id, None)

    def _close(self, sub: Subscription) -> None:
        # A full queue while closing is the normal case, not an error: the
        # sentinel cannot be delivered and the generator ends with the
        # connection anyway.
        with suppress(asyncio.QueueFull):
            sub.queue.put_nowait(None)
        self.unsubscribe(sub)

    def listeners(self, run_id: str) -> int:
        return len(self._subs.get(run_id, ()))
