"""Starting an investigation, watching it happen, and stopping it.

The run lifecycle lives beside the endpoints that drive it because it has
exactly two callers — this controller and the benchmark batch — and no second
seam to justify. What it owns is the four things a service has to get right
that a handler cannot:

**A run is a background task, not a request.** A case takes 20 to 90 seconds;
the POST returns 202 with a run id and two URLs, and everything after that
reaches the client over the stream.

**The events are journalled before they are published.** That is what makes
``Last-Event-ID`` exact: a client that reconnects with ``42`` gets ``seq > 42``
from the journal and then attaches live, with no gap and no duplicate. The
subscription is opened *before* the replay is read, so an event published
during the replay waits in the queue rather than falling between the two.

**One run per case at a time.** A second run on the same case would race the
first for ``cases/<case_id>.json``, and the file is the graded artefact.
``force`` cancels the first rather than running both.

**The answer still goes through ``CaseRunner``.** It is the only thing in the
system that writes to ``cases/``, quarantining anything invalid. An HTTP run
that wrote its own file would be a second implementation of that rule, and the
one that skipped the quarantine. ``CaseRunner`` builds its own collecting
emitter for the trace file, so the journalling emitter is *teed* onto it
rather than replacing it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar

from fastapi import Query, Request
from sqlalchemy import func, select, update
from starlette.responses import StreamingResponse

from api.container import Container, RunHandle
from api.controller import ApiController, route
from api.errors import conflict, not_found
from api.schemas import (
    BudgetSnapshot,
    EventEnvelope,
    EventPage,
    Page,
    RunAccepted,
    RunCounters,
    RunDetail,
    RunError,
    RunMode,
    RunStatus,
    RunSummary,
    StartInvestigationRequest,
)
from api.sse import KEEPALIVE, RETRY_HINT, JournalingEventEmitter, format_sse
from api.sse.journal import SseEnvelope
from api.store import Run, RunEvent, new_id, utcnow
from sentinel.agents.emitter import TERMINAL_EVENTS, EventEmitter
from sentinel.agents.orchestrator import InvestigationOrchestrator, InvestigationResult
from sentinel.domain.alert import Alert
from sentinel.domain.errors import SentinelError
from sentinel.runner import CaseRunner

logger = logging.getLogger(__name__)

#: A run whose process died is not running. Rows left mid-flight by a restart
#: are closed at start-up so the console does not show a run nobody is driving.
RESTART_CODE = "process_restarted"

#: What a cancelled run puts on the stream. The journal needs a terminal event
#: or a listening console waits forever for one.
CANCELLED_CODE = "cancelled"

#: The longest pause a paced replay will take between two events. A run with a
#: 40-second gap in it should still be watchable at ``speed=1``.
MAX_REPLAY_GAP_S = 3.0

#: Statuses a run does not come back from.
_FINISHED = frozenset({RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED})


class TeeEmitter(EventEmitter):
    """One event stream, two sinks.

    Responsibility: fan out and nothing else. It exists because ``CaseRunner``
    builds a ``CollectingEmitter`` to write the trace file beside the answer,
    and an HTTP run needs those same events on the journal — replacing the
    runner's emitter would cost the trace file, and running the case twice is
    not a serious alternative.
    Collaborators: a ``JournalingEventEmitter`` and whatever the runner passed.
    """

    def __init__(self, *sinks: EventEmitter) -> None:
        self._sinks = sinks

    def emit(self, event_type: str, payload: dict[str, Any], step: int | None = None) -> None:
        self._check(event_type)
        for sink in self._sinks:
            sink.emit(event_type, dict(payload), step)


class JournalledRun(InvestigationOrchestrator):
    """The configured orchestrator, with the journal attached to its output.

    Responsibility: guarantee that a run started over HTTP journals, whatever
    emitter its caller supplies. Collaborators: the real orchestrator, which
    is untouched and unaware.
    """

    def __init__(self, inner: InvestigationOrchestrator, journal: EventEmitter) -> None:
        self._inner = inner
        self._journal = journal

    async def run(self, alert: Alert, emitter: EventEmitter | None = None) -> InvestigationResult:
        sink = TeeEmitter(self._journal, emitter) if emitter is not None else self._journal
        return await self._inner.run(alert, sink)


class InvestigationService:
    """The life of one run: admitted, started, journalled, finished, recorded.

    Responsibility: admission control, the background task, and keeping the
    ``run`` row honest about what the task is doing. It makes no fraud
    decision and writes no answer file — ``CaseRunner`` does that.
    Collaborators: ``Container`` for the orchestrator, the journal and the
    broker; ``RunRegistry`` for what is in flight; ``CaseRunner`` for the disk.
    """

    def __init__(self, container: Container) -> None:
        self._c = container

    async def recover(self) -> int:
        """Close runs a previous process left mid-flight.

        Their tasks died with that process, so the rows are the only thing
        still claiming they are running. Left alone they make the queue show
        an investigation nobody is driving and never will be.
        """
        async with self._c.db.session() as session:
            result = await session.execute(
                update(Run)
                .where(Run.status.in_((RunStatus.QUEUED.value, RunStatus.RUNNING.value)))
                .values(
                    status=RunStatus.FAILED.value,
                    finished_at=utcnow(),
                    error_code=RESTART_CODE,
                    error_message="the process serving this run restarted",
                )
            )
        # rowcount is on the DBAPI cursor, which the async Result does not
        # re-declare; it is there at runtime for an UPDATE.
        closed = int(getattr(result, "rowcount", 0) or 0)
        if closed:
            logger.info("closed %d run(s) left in flight by a previous process", closed)
        return closed

    async def start(
        self,
        request: StartInvestigationRequest,
        *,
        batch_id: str | None = None,
    ) -> RunAccepted:
        """Admit one investigation and hand back where to watch it."""
        alert = self._alert(request.case_id)
        orchestrator = self._c.orchestrator_for(request.mode)
        await self._make_room(alert.alert_id, force=request.force)

        run_id = new_id("run")
        handle = RunHandle(
            run_id=run_id, case_id=alert.alert_id, started_at=time.monotonic(), batch_id=batch_id
        )
        # Admitted before the row is written: the 429 is an admission decision
        # and a refused run should leave no trace of having nearly happened.
        self._c.runs.admit(handle)
        name = request.orchestrator or type(orchestrator).__name__
        async with self._c.db.session() as session:
            session.add(
                Run(
                    run_id=run_id,
                    case_id=alert.alert_id,
                    status=RunStatus.QUEUED.value,
                    mode=request.mode.value,
                    orchestrator=name,
                    started_at=utcnow(),
                    batch_id=batch_id,
                )
            )

        emitter = JournalingEventEmitter(run_id, alert.alert_id, self._c.journal, self._c.broker)
        emitter.start()
        handle.emitter = emitter
        handle.task = asyncio.create_task(
            self._drive(handle, alert, orchestrator, emitter), name=f"run-{run_id}"
        )
        logger.info("investigation %s started on %s (%s)", run_id, alert.alert_id, name)
        return RunAccepted(
            run_id=run_id,
            case_id=alert.alert_id,
            status=RunStatus.QUEUED,
            stream_url=f"/api/investigations/{run_id}/events",
            events_url=f"/api/investigations/{run_id}/events.json",
        )

    async def cancel(self, run_id: str) -> RunSummary:
        """Stop a run in flight. A finished one is a 409, not a no-op."""
        handle = self._c.runs.get(run_id)
        if handle is None:
            row = await self.row(run_id)
            raise conflict(
                f"run '{run_id}' is not in flight; it is {row.status}",
                code="run_not_active",
                run_id=run_id,
                run_status=row.status,
            )
        handle.cancelled = True
        if handle.task is not None:
            handle.task.cancel()
            # Wait for the task to unwind so the row and the journal are
            # settled before the 202 goes out; otherwise a console that
            # immediately re-reads the run sees it still running.
            with contextlib.suppress(asyncio.CancelledError):
                await handle.task
        return run_summary(await self.row(run_id))

    # ── reads ────────────────────────────────────────────────────────────────

    async def row(self, run_id: str) -> Run:
        async with self._c.db.session() as session:
            run = await session.get(Run, run_id)
        if run is None:
            raise not_found("run", run_id)
        return run

    async def rows(
        self,
        *,
        case_id: str = "",
        status: RunStatus | None = None,
        batch_id: str | None = None,
        limit: int = 50,
    ) -> tuple[Run, ...]:
        criteria = []
        if case_id:
            criteria.append(Run.case_id == case_id)
        if status is not None:
            criteria.append(Run.status == status.value)
        if batch_id:
            criteria.append(Run.batch_id == batch_id)
        async with self._c.db.session() as session:
            rows = (
                await session.scalars(
                    select(Run)
                    .where(*criteria)
                    .order_by(Run.started_at.desc(), Run.run_id.desc())
                    .limit(max(1, min(limit, 200)))
                )
            ).all()
        return tuple(rows)

    async def steps_of(self, run_ids: Sequence[str]) -> dict[str, int]:
        """The step each run has reached, from the journal rather than the row.

        The row is written when the run finishes; the journal is written as it
        goes, so this is the only source that is right for a run in flight —
        and one grouped query answers it for a whole page of runs.
        """
        if not run_ids:
            return {}
        async with self._c.db.session() as session:
            rows = (
                await session.execute(
                    select(RunEvent.run_id, func.max(RunEvent.step))
                    .where(RunEvent.run_id.in_(list(run_ids)))
                    .group_by(RunEvent.run_id)
                )
            ).all()
        return {str(run_id): int(step or 0) for run_id, step in rows}

    async def budget_of(self, run_id: str) -> BudgetSnapshot:
        """The latest budget the run published, live or finished."""
        async with self._c.db.session() as session:
            payload = await session.scalar(
                select(RunEvent.payload)
                .where(RunEvent.run_id == run_id, RunEvent.type == "budget.updated")
                .order_by(RunEvent.seq.desc())
                .limit(1)
            )
        return BudgetSnapshot.model_validate(payload or {})

    async def events(self, run_id: str) -> int:
        return await self._c.journal.count(run_id)

    # ── the background task ──────────────────────────────────────────────────

    async def _drive(
        self,
        handle: RunHandle,
        alert: Alert,
        orchestrator: InvestigationOrchestrator,
        emitter: JournalingEventEmitter,
    ) -> None:
        """Run one case to a file on disk, and keep the row saying what is true."""
        await self._mark(handle.run_id, status=RunStatus.RUNNING)
        runner = CaseRunner(
            settings=self._c.settings,
            orchestrator=JournalledRun(orchestrator, emitter),
            run_id=handle.run_id,
            checker=self._c.checker,
        )
        try:
            outcome = await runner.run_one(alert)
        except asyncio.CancelledError:
            # The only terminal event a cancelled run will ever emit. Without
            # it every open stream on this run waits for one that is not coming.
            emitter.emit(
                "run.failed",
                {
                    "code": CANCELLED_CODE,
                    "message": "cancelled by an operator",
                    "recoverable": True,
                },
            )
            await emitter.aclose()
            await self._mark(
                handle.run_id,
                status=RunStatus.CANCELLED,
                error_code=CANCELLED_CODE,
                error_message="cancelled by an operator",
            )
            raise
        except SentinelError as exc:
            # CaseRunner absorbs a SentinelError into its outcome, so reaching
            # here means the failure was outside the investigation itself.
            emitter.emit(
                "run.failed",
                {"code": exc.code, "message": exc.message, "recoverable": False},
            )
            await emitter.aclose()
            await self._mark(
                handle.run_id,
                status=RunStatus.FAILED,
                error_code=exc.code,
                error_message=exc.message,
            )
            raise
        else:
            # Drain BEFORE the row says the run finished, for two reasons. A
            # client that polls the row and then fetches the journal would
            # otherwise race the tail of its own run; and `_finish` reads the
            # step number and the budget back *out* of the journal, so a
            # half-drained one gives it the wrong answers.
            await emitter.aclose()
            await self._finish(handle.run_id, outcome)
        finally:
            # Idempotent: every branch above has already drained, and this
            # catches any path that did not reach one.
            await emitter.aclose()
            self._c.runs.release(handle.run_id)

    async def _finish(self, run_id: str, outcome: Any) -> None:
        answer = outcome.answer
        code, message = ("", "") if outcome.ok else _split_error(outcome.error)
        await self._mark(
            run_id,
            status=RunStatus.COMPLETED if answer is not None else RunStatus.FAILED,
            error_code=code,
            error_message=message,
            tool_calls=answer.tool_calls if answer else 0,
            tokens=answer.tokens if answer else 0,
            latency_s=answer.latency_s if answer else 0.0,
            validation_ok=outcome.ok,
            step=(await self.steps_of([run_id])).get(run_id, 0),
            budget=(await self.budget_of(run_id)).model_dump(mode="json"),
        )

    async def _mark(self, run_id: str, *, status: RunStatus, **fields: Any) -> None:
        values: dict[str, Any] = {"status": status.value, **fields}
        if status in _FINISHED:
            values["finished_at"] = utcnow()
        async with self._c.db.session() as session:
            await session.execute(update(Run).where(Run.run_id == run_id).values(**values))

    # ── admission ────────────────────────────────────────────────────────────

    def _alert(self, case_id: str) -> Alert:
        alert = self._c.alerts.get(case_id)
        if alert is None:
            raise not_found("case", case_id)
        return alert

    async def _make_room(self, case_id: str, *, force: bool) -> None:
        running = self._c.runs.for_case(case_id)
        if running is None:
            return
        if not force:
            raise conflict(
                f"an investigation of '{case_id}' is already running",
                code="run_already_active",
                case_id=case_id,
                run_id=running.run_id,
            )
        # Two runs on one case would race for cases/<case_id>.json, and that
        # file is the graded artefact. force means "replace", not "also".
        logger.info("force: cancelling %s to restart %s", running.run_id, case_id)
        await self.cancel(running.run_id)


class InvestigationsController(ApiController):
    """The six endpoints of a run, including the stream the console lives on.

    Responsibility: HTTP shape only — the lifecycle is
    :class:`InvestigationService` and the replay guarantee is ``EventJournal``.
    Collaborators: ``SseBroker`` for the live half of the stream.
    """

    prefix = "/investigations"
    tags: ClassVar[list[str]] = ["investigations"]

    def routes(self) -> list[dict[str, Any]]:
        return [
            route(
                "",
                self.start,
                methods=["POST"],
                status_code=202,
                summary="Start an investigation in the background.",
                errors={
                    404: "No such case in the pack.",
                    409: "A run is already active on this case; pass force.",
                    422: "This deployment cannot serve that mode.",
                    429: "The concurrent-investigation ceiling is full.",
                },
            ),
            route("", self.list, summary="Recent runs, newest first."),
            route(
                "/{run_id}",
                self.detail,
                summary="One run, with its counters, budget and error.",
                errors={404: "No such run."},
            ),
            route(
                "/{run_id}/events",
                self.events,
                summary="The live SSE stream, replayed from Last-Event-ID.",
                response_class=StreamingResponse,
                errors={404: "No such run."},
            ),
            route(
                "/{run_id}/events.json",
                self.events_json,
                summary="The journal as JSON, for a client that would rather poll.",
                errors={404: "No such run."},
            ),
            route(
                "/{run_id}/cancel",
                self.cancel,
                methods=["POST"],
                status_code=202,
                summary="Stop a run in flight.",
                errors={404: "No such run.", 409: "The run is not in flight."},
            ),
        ]

    # ── endpoints ────────────────────────────────────────────────────────────

    async def start(self, body: StartInvestigationRequest) -> RunAccepted:
        """202 with the run id and the two URLs it can be watched on."""
        return await self._service().start(body)

    async def list(
        self,
        case_id: str = Query(default=""),
        status: RunStatus | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> Page[RunSummary]:
        service = self._service()
        rows = await service.rows(case_id=case_id, status=status, limit=limit)
        steps = await service.steps_of([row.run_id for row in rows])
        return Page[RunSummary](
            items=[run_summary(row, steps.get(row.run_id)) for row in rows], total=len(rows)
        )

    async def detail(self, run_id: str) -> RunDetail:
        service = self._service()
        row = await service.row(run_id)
        steps = await service.steps_of([run_id])
        return RunDetail(
            run_id=row.run_id,
            case_id=row.case_id,
            status=RunStatus(row.status),
            step=steps.get(run_id, row.step),
            mode=RunMode(row.mode),
            orchestrator=row.orchestrator,
            started_at=_aware(row.started_at) or utcnow(),
            finished_at=_aware(row.finished_at),
            counters=RunCounters(
                tool_calls=row.tool_calls,
                tokens=row.tokens,
                usd=row.usd,
                latency_s=row.latency_s,
            ),
            budget=await service.budget_of(run_id),
            error=(
                RunError(code=row.error_code, message=row.error_message) if row.error_code else None
            ),
            events=await service.events(run_id),
        )

    async def events(
        self,
        run_id: str,
        request: Request,
        last_event_id: int = Query(default=0, ge=0),
        speed: float | None = Query(default=None, gt=0.0, le=100.0),
    ) -> StreamingResponse:
        """The stream: replay what was missed, then attach live.

        ``Last-Event-ID`` is read from the header when the client can send one
        and from the query when it cannot — the browser's native
        ``EventSource`` can send neither that nor the role header, which is why
        the console uses ``fetch`` and why both fallbacks exist (ADR-5).
        """
        service = self._service()
        await service.row(run_id)  # 404 before a stream is opened, not during it
        resume = _resume_from(request, last_event_id)
        return StreamingResponse(
            self._stream(run_id, resume, speed),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                # nginx buffers a proxied response by default, which turns a
                # live stream into one delivery at the end of the run.
                "X-Accel-Buffering": "no",
            },
        )

    async def events_json(
        self,
        run_id: str,
        from_seq: int = Query(default=0, ge=0, alias="from"),
        to_seq: int | None = Query(default=None, ge=0, alias="to"),
        limit: int = Query(default=1000, ge=1, le=5000),
    ) -> EventPage:
        """The same journal, paged, for a client that would rather poll."""
        service = self._service()
        await service.row(run_id)
        envelopes = await self.container.journal.since(run_id, from_seq, limit=limit)
        if to_seq is not None:
            envelopes = [e for e in envelopes if e.seq <= to_seq]
        events = [EventEnvelope.model_validate(e.as_dict()) for e in envelopes]
        # `next` is the cursor to ask for next, and is the same number a client
        # would send as Last-Event-ID — so switching between polling and
        # streaming needs no translation.
        more = len(envelopes) == limit
        return EventPage(events=events, next=events[-1].seq if events and more else None)

    async def cancel(self, run_id: str) -> RunSummary:
        """202 with the run's new state — which is what a caller wants back."""
        return await self._service().cancel(run_id)

    # ── the stream itself ────────────────────────────────────────────────────

    async def _stream(self, run_id: str, resume: int, speed: float | None) -> AsyncIterator[str]:
        container = self.container
        keepalive = float(container.settings.sse_keepalive_seconds)
        # Subscribe *before* reading the journal. An event published while the
        # replay is being read then waits in this queue instead of falling into
        # the gap between the two halves.
        subscription = container.broker.subscribe(run_id)
        try:
            yield RETRY_HINT
            cursor = resume
            for envelope in await container.journal.since(run_id, resume):
                if speed is not None:
                    await asyncio.sleep(_gap(cursor and envelope, envelope, speed))
                yield format_sse(envelope)
                cursor = envelope.seq
                if envelope.type in TERMINAL_EVENTS:
                    return
            if await self._finished(run_id):
                # Finished, and the replay held no terminal event — a run the
                # process lost. Close rather than hold the connection open for
                # an event that will never be published.
                return
            while True:
                try:
                    item = await asyncio.wait_for(subscription.queue.get(), timeout=keepalive)
                except TimeoutError:
                    # A comment, not an event: it takes no id and cannot open a
                    # gap for a client that reconnects after it.
                    yield KEEPALIVE
                    continue
                if item is None:
                    return
                if item.seq <= cursor:
                    continue  # already replayed; a duplicate would break the cursor
                cursor = item.seq
                yield format_sse(item)
                if item.type in TERMINAL_EVENTS:
                    return
        finally:
            container.broker.unsubscribe(subscription)

    async def _finished(self, run_id: str) -> bool:
        row = await self._service().row(run_id)
        return RunStatus(row.status) in _FINISHED

    def _service(self) -> InvestigationService:
        # Cheap and stateless: every piece of state it touches — the registry,
        # the journal, the database — belongs to the container.
        return InvestigationService(self.container)


# ── helpers shared with the other controllers ────────────────────────────────


def run_summary(row: Run, step: int | None = None) -> RunSummary:
    """One ``run`` row as the wire carries it.

    Built field by field rather than by ``model_validate(row)`` for one
    reason: SQLite hands back a naive datetime even from a timezone-aware
    column, and a timestamp without a ``Z`` is one the browser reads as local.
    """
    return RunSummary(
        run_id=row.run_id,
        case_id=row.case_id,
        status=RunStatus(row.status),
        step=row.step if step is None else step,
        mode=RunMode(row.mode),
        orchestrator=row.orchestrator,
        started_at=_aware(row.started_at) or utcnow(),
        finished_at=_aware(row.finished_at),
        tool_calls=row.tool_calls,
        tokens=row.tokens,
        usd=row.usd,
        latency_s=row.latency_s,
        validation_ok=row.validation_ok,
        error_code=row.error_code,
        batch_id=row.batch_id,
    )


def latest_per_case(rows: Iterable[Run]) -> dict[str, Run]:
    """The newest run for each case, from rows already ordered newest first."""
    newest: dict[str, Run] = {}
    for row in rows:
        newest.setdefault(row.case_id, row)
    return newest


def _aware(moment: datetime | None) -> datetime | None:
    """SQLite gives back naive datetimes; the wire contract is UTC-aware."""
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _resume_from(request: Request, query_value: int) -> int:
    """``Last-Event-ID``, from the header if there is one, else the query."""
    header = request.headers.get("Last-Event-ID", "").strip()
    if header.isdigit():
        return int(header)
    return query_value


def _gap(previous: SseEnvelope | Any, current: SseEnvelope, speed: float) -> float:
    """The pause before an event when a finished run is being replayed to a human."""
    if not isinstance(previous, SseEnvelope):
        return 0.0
    try:
        before = datetime.fromisoformat(previous.at.replace("Z", "+00:00"))
        after = datetime.fromisoformat(current.at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return min(max((after - before).total_seconds(), 0.0), MAX_REPLAY_GAP_S) / speed


def _split_error(error: str) -> tuple[str, str]:
    """``"budget_exceeded: 25 tool calls"`` as the two columns the row holds."""
    code, _, message = error.partition(": ")
    return (code, message) if message else ("run_failed", error)
