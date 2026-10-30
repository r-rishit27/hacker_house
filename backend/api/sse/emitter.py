"""The orchestrator's events, journaled and published.

``EventEmitter.emit`` is synchronous because a step should not have to await
to say what it did. Persistence is not, so events are queued here and drained
by one task per run. The queue preserves order, and the drain journals before
it publishes, so the guarantee that a subscriber is never ahead of the journal
survives the asynchrony.

The emitter also injects ``step`` into the payload body. The orchestrator
passes it as an envelope field, but the console's timeline groups rows by
``payload.step`` and the generated contract types it that way, so one of the
two had to give. Doing it here means no step has to remember.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from api.sse.journal import EventJournal, SseBroker, SseEnvelope
from sentinel.agents.emitter import TERMINAL_EVENTS, EventEmitter

logger = logging.getLogger(__name__)


class JournalingEventEmitter(EventEmitter):
    """Persists every event, then publishes it to whoever is listening.

    Responsibility: ordering and durability. It makes no decision about what an
    event means; the only event type it treats specially is a terminal one,
    which closes the stream.
    Collaborators: ``EventJournal`` for the row, ``SseBroker`` for the fan-out.
    """

    def __init__(
        self,
        run_id: str,
        case_id: str,
        journal: EventJournal,
        broker: SseBroker,
    ) -> None:
        self._run_id = run_id
        self._case_id = case_id
        self._journal = journal
        self._broker = broker
        self._pending: asyncio.Queue[tuple[str, dict[str, Any], int | None] | None] = (
            asyncio.Queue()
        )
        self._drain: asyncio.Task[None] | None = None
        self._closed = False

    def start(self) -> None:
        """Begin draining. Called once, by the run service, inside the loop."""
        if self._drain is None:
            self._drain = asyncio.create_task(self._run(), name=f"sse-drain-{self._run_id}")

    def emit(self, event_type: str, payload: dict[str, Any], step: int | None = None) -> None:
        self._check(event_type)
        if self._closed:
            return
        self._pending.put_nowait((event_type, dict(payload), step))

    async def aclose(self) -> None:
        """Drain what is queued, then end every open connection."""
        if self._closed:
            return
        self._closed = True
        self._pending.put_nowait(None)
        if self._drain is not None:
            await self._drain
        self._broker.close(self._run_id)
        self._journal.forget(self._run_id)

    async def _run(self) -> None:
        while True:
            item = await self._pending.get()
            if item is None:
                return
            event_type, payload, step = item
            if step is not None:
                # The contract types `step` inside the payload; the orchestrator
                # passes it beside. One of the two had to give, and a step that
                # has to remember is a step that will forget.
                payload.setdefault("step", step)
            try:
                envelope = await self._journal.append(
                    self._run_id, self._case_id, event_type, payload, step
                )
            except Exception:
                logger.exception("could not journal %s on %s", event_type, self._run_id)
                continue
            self._broker.publish(self._run_id, envelope)
            if event_type in TERMINAL_EVENTS:
                # Drain anything still queued behind the terminal event before
                # the connection closes, rather than losing it.
                await asyncio.sleep(0)


def format_sse(envelope: SseEnvelope) -> str:
    """One event in the ``text/event-stream`` wire format.

    ``id:`` is the sequence number, which is what makes ``Last-Event-ID``
    resumption a range scan rather than a guess.
    """
    data = json.dumps(envelope.as_dict(), ensure_ascii=False, default=str)
    return f"id: {envelope.seq}\nevent: {envelope.type}\ndata: {data}\n\n"


#: Sent every `sse_keepalive_seconds` so a proxy does not close an idle stream.
#: A comment, so it is not an event, takes no id, and cannot open a gap.
KEEPALIVE = ": keepalive\n\n"

#: The first line of every stream: how long a browser waits before reconnecting.
RETRY_HINT = "retry: 2000\n\n"
