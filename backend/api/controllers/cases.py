"""The queue, the case, and everything hanging off it.

One property shapes this whole controller: **all twenty cases exist from the
first request.** The queue is the case pack, not the answer directory, and for
most of a benchmark run most of those cases have no answer file yet. A console
that could only render a finished case would show an empty screen for the six
minutes that are the most interesting to watch, so `CaseSummary` makes every
answer-derived field optional and this controller joins rather than filters.

The SAR endpoint returns 200 even when `sar.file` is false. That is not an
oversight: "we considered a report and here is why we are not filing one" is
the answer an examiner wants, and a 404 would make the absence of a filing
indistinguishable from the absence of a case.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from fastapi import Query, Request
from fastapi.responses import PlainTextResponse

from api.controller import ApiController, route
from api.controllers.actions import plan_body
from api.errors import ApiError, not_found
from api.schemas import (
    ActionPlan,
    CaseDetail,
    CasePage,
    CaseSort,
    CaseSummary,
    MemoryBody,
    PostingView,
    RetrievalBreakdown,
    RetrievalHit,
    RetrievalKind,
    RunSummary,
    SarBody,
    ToolCalledPayload,
    TraceBody,
    TraceStep,
    ValidationBody,
    WriteToGraphRequest,
    WriteToGraphResult,
)
from api.sse.journal import SseEnvelope
from sentinel.domain.alert import Alert
from sentinel.domain.answer import AnswerFile
from sentinel.domain.enums import CaseStatus, TriggerType, Verdict
from sentinel.memory.store import CaseWriteRequest, is_closed_case


class CasesController(ApiController):
    """Everything addressed by a case id.

    Responsibility: join the case pack to whatever exists for each case — an
    answer, a run, a trace, a validation — and shape it. It computes no
    verdict and decides no route.
    Collaborators: the container's ``alerts`` (the pack), ``answers`` (the
    directory), the store (runs and journals), ``AnswerValidator``,
    ``GraphIdentityChecker`` and ``CaseMemoryStore``.
    """

    prefix = "/cases"
    tags: ClassVar[list[str]] = ["cases"]

    def routes(self) -> list[dict[str, Any]]:
        return [
            route("", self.list_cases, summary="The work queue: all 20, answered or not."),
            route(
                "/{case_id}",
                self.detail,
                summary="The case, its answer, its last run and its validation.",
                errors={404: "No such case in the pack."},
            ),
            route(
                "/{case_id}/answer",
                self.answer,
                summary="The answer file, verbatim, exactly as it is on disk.",
                errors={404: "No answer has been written for this case yet."},
            ),
            route(
                "/{case_id}/trace",
                self.trace,
                summary="The journalled steps and postings of one run.",
                errors={404: "No run has been journalled for this case."},
            ),
            route(
                "/{case_id}/validation",
                self.validation,
                summary="Re-validate the answer on disk, optionally against the graph.",
                errors={404: "No answer has been written for this case yet."},
            ),
            route(
                "/{case_id}/sar",
                self.sar,
                summary="The report, or the reasoned decision not to file one. Always 200.",
                errors={404: "No answer has been written for this case yet."},
            ),
            route(
                "/{case_id}/sar.txt",
                self.sar_text,
                summary="The same filing, rendered for a human to read.",
                response_class=PlainTextResponse,
                errors={404: "No answer has been written for this case yet."},
            ),
            route(
                "/{case_id}/memory",
                self.memory,
                summary="What this case retrieved, and what it cited.",
                errors={404: "No answer has been written for this case yet."},
            ),
            route(
                "/{case_id}/actions",
                self.actions,
                summary="The action plan with every route recomputed. ADR-6.",
                errors={404: "No answer has been written for this case yet."},
            ),
            route(
                "/{case_id}/write-to-graph",
                self.write_to_graph,
                methods=("POST",),
                status_code=202,
                summary="Upsert the FraudCase vertex and its eight edge types.",
                errors={
                    404: "No answer has been written for this case yet.",
                    409: "Already written; pass force to write again.",
                    503: "The graph is unreachable or waking.",
                },
            ),
        ]

    # ── the queue ────────────────────────────────────────────────────────────

    async def list_cases(
        self,
        status: CaseStatus | None = None,
        verdict: Verdict | None = None,
        trigger_type: TriggerType | None = None,
        sort: CaseSort = CaseSort.OPENED_AT,
        q: str = "",
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> CasePage:
        """Every alert in the pack, joined to whatever exists for it."""
        rows = [await self._summary(alert) for alert in self.container.alerts.values()]
        rows = [row for row in rows if _matches(row, status, verdict, trigger_type, q)]
        rows.sort(key=_sort_key(sort), reverse=sort is not CaseSort.OPENED_AT)
        return CasePage(items=rows[offset : offset + limit], total=len(rows))

    async def detail(self, case_id: str, request: Request) -> CaseDetail:
        alert = self._alert(case_id)
        answer = await self.container.answers.load(case_id)
        run = await self._last_run(case_id)
        validation = (
            ValidationBody.model_validate(
                self.container.validator.validate(answer.model_dump(mode="json"))
            )
            if answer is not None
            else None
        )
        principal = self.principal(request)
        plan = None
        if answer is not None:
            plan = plan_body(
                await self.container.executions.plan(case_id, principal), principal.role
            )
        return CaseDetail(
            alert=alert,
            answer=answer,
            run=run,
            validation=validation,
            trace_available=(run is not None and await self._has_journal(run.run_id))
            or self.container.traces.has(case_id),
            actions=plan,
        )

    async def answer(self, case_id: str) -> AnswerFile:
        """The graded file itself, unmodified. What a judge downloads."""
        return await self._answer(case_id)

    # ── the run's own record ─────────────────────────────────────────────────

    async def trace(self, case_id: str, run_id: str = "") -> TraceBody:
        self._alert(case_id)
        resolved = run_id or await self._last_run_id(case_id)
        if resolved:
            events = await self.container.journal.since(resolved, 0)
            if events:
                return _trace_from(case_id, resolved, events)
        # Nothing in this process journalled it. The twenty graded answers were
        # produced by the batch runner, whose journal is on disk — replaying it
        # is what makes the investigation tab show the run that actually
        # happened rather than nothing at all.
        archived = self.container.traces.latest(
            case_id, fraud_probability=await self._settled_probability(case_id)
        )
        if archived is None:
            raise not_found("trace for case", case_id)
        return _trace_from(case_id, archived.run_id, archived.events)

    async def validation(self, case_id: str, graph: bool = True) -> ValidationBody:
        """Re-run the validator now, rather than serving what the run recorded.

        A judge editing an answer by hand wants the verdict on the file as it
        is, and the checker is the same object the runner uses.
        """
        answer = await self._answer(case_id)
        payload = answer.model_dump(mode="json")
        report = self.container.validator.validate(payload)
        checked = False
        checker = self.container.checker
        if graph and checker is not None:
            await checker.check(payload, report)
            checked = True
        body = ValidationBody.model_validate(report)
        return body.model_copy(update={"graph_checked": checked})

    # ── the report ───────────────────────────────────────────────────────────

    async def sar(self, case_id: str) -> SarBody:
        answer = await self._answer(case_id)
        return SarBody(case_id=case_id, sar=answer.sar, rendered=_render_sar(case_id, answer))

    async def sar_text(self, case_id: str) -> str:
        answer = await self._answer(case_id)
        return _render_sar(case_id, answer)

    # ── memory and actions ───────────────────────────────────────────────────

    async def memory(self, case_id: str) -> MemoryBody:
        """What this case retrieved, split by where it came from.

        Read off the answer file rather than re-retrieved: the question is
        what *this investigation* used, and re-running the retriever now would
        answer a different one — the corpus has grown by every case since.
        """
        answer = await self._answer(case_id)
        cited = list(answer.case.similar_prior_cases)
        structural, vector = await self._retrieval_provenance(case_id)
        by_id = {hit.id: hit for hit in (*structural, *vector)}
        return MemoryBody(
            case_id=case_id,
            prior_closed_cases=[
                by_id.get(c) or RetrievalHit(id=c, kind=RetrievalKind.CLOSED_CASE)
                for c in cited
                if is_closed_case(c)
            ],
            sentinel_cases=[
                by_id.get(c) or RetrievalHit(id=c, kind=RetrievalKind.SENTINEL_CASE)
                for c in cited
                if not is_closed_case(c)
            ],
            cited=[e.ref for e in answer.case.evidence if e.ref.startswith("doc:")],
            retrieval=RetrievalBreakdown(structural=structural, vector=vector),
        )

    async def actions(self, case_id: str, request: Request) -> ActionPlan:
        """The plan with the routes the routing table returns *now*."""
        await self._answer(case_id)
        principal = self.principal(request)
        return plan_body(await self.container.executions.plan(case_id, principal), principal.role)

    # ── write-back ───────────────────────────────────────────────────────────

    async def write_to_graph(
        self, case_id: str, body: WriteToGraphRequest, request: Request
    ) -> WriteToGraphResult:
        """Upsert the `FraudCase` for an answer already on disk.

        The agent writes this during a run. This endpoint exists for the case
        that was validated by hand, or whose write-back failed while the
        workspace was waking.
        """
        alert = self._alert(case_id)
        answer = await self._answer(case_id)
        if answer.case.written_to_graph and not body.force:
            raise ApiError(
                f"{case_id} is already written as {answer.case.graph_case_id}; "
                "pass force to write it again",
                code="already_written",
                status=409,
                graph_case_id=answer.case.graph_case_id,
            )
        cited = list(answer.case.similar_prior_cases)
        result = await self.container.memory.write(
            CaseWriteRequest(
                case_id=case_id,
                answer=answer,
                alert_id=alert.alert_id,
                customer_id=alert.customer_id,
                card_id=alert.card_id,
                device_keys=list(answer.case.connected_device_profiles),
                cites_closed_cases=[c for c in cited if is_closed_case(c)],
                cites_cases=[c for c in cited if not is_closed_case(c)],
                applied_rules=[
                    e.entity_ids[0]
                    for e in answer.case.evidence
                    if e.source.value == "document" and e.entity_ids
                ],
                created_at=alert.opened_at,
                created_by=self.principal(request).id,
            )
        )
        return WriteToGraphResult.of(result)

    # ── internals ────────────────────────────────────────────────────────────

    def _alert(self, case_id: str) -> Alert:
        alert = self.container.alerts.get(case_id)
        if alert is None:
            raise not_found("case", case_id)
        return alert

    async def _answer(self, case_id: str) -> AnswerFile:
        self._alert(case_id)
        answer = await self.container.answers.load(case_id)
        if answer is None:
            raise ApiError(
                f"no answer has been written for {case_id} yet",
                code="answer_not_written",
                status=404,
            )
        return answer

    async def _summary(self, alert: Alert) -> CaseSummary:
        return CaseSummary.of(
            alert,
            await self.container.answers.load(alert.alert_id),
            await self._last_run(alert.alert_id),
        )

    async def _last_run(self, case_id: str) -> RunSummary | None:
        from sqlalchemy import desc, select

        from api.store.models import Run

        async with self.container.db.session() as session:
            row = await session.scalar(
                select(Run).where(Run.case_id == case_id).order_by(desc(Run.started_at)).limit(1)
            )
        return RunSummary.model_validate(row) if row is not None else None

    async def _last_run_id(self, case_id: str) -> str:
        run = await self._last_run(case_id)
        return run.run_id if run is not None else ""

    async def _has_journal(self, run_id: str) -> bool:
        return await self.container.journal.count(run_id) > 0

    async def _settled_probability(self, case_id: str) -> float | None:
        """What the served answer settled on, which identifies its run."""
        answer = await self.container.answers.load(case_id)
        return answer.case.fraud_probability if answer is not None else None

    async def _events(self, case_id: str) -> Sequence[SseEnvelope]:
        """This case's journal, from memory if this process ran it, else disk.

        The two are the same events: the archive stores exactly what the live
        stream carried. Preferring memory means a run happening right now wins
        over the recording of an older one.
        """
        run_id = await self._last_run_id(case_id)
        if run_id:
            events = await self.container.journal.since(run_id, 0)
            if events:
                return events
        archived = self.container.traces.latest(
            case_id, fraud_probability=await self._settled_probability(case_id)
        )
        return archived.events if archived is not None else []

    async def _retrieval_provenance(
        self, case_id: str
    ) -> tuple[list[RetrievalHit], list[RetrievalHit]]:
        """The hits this case's last run journalled, split by which ranking found them.

        Split on the hit's own recorded provenance rather than on which
        `retrieval.completed` event carried it: the fused list carries both,
        and which ranking found a case is the thing the memory tab is for.
        """
        structural: list[RetrievalHit] = []
        vector: list[RetrievalHit] = []
        events = await self._events(case_id)
        if not events:
            return structural, vector
        for event in events:
            if event.type != "retrieval.completed":
                continue
            for raw in event.payload.get("hits") or ():
                if not isinstance(raw, dict):
                    continue
                hit = RetrievalHit.model_validate(raw)
                found_by = str(hit.provenance.get("strategy") or "structural")
                if "vector" in found_by:
                    vector.append(hit)
                if "structural" in found_by or "vector" not in found_by:
                    structural.append(hit)
        return structural, vector


# ── plain functions ──────────────────────────────────────────────────────────


def _trace_from(case_id: str, run_id: str, events: Sequence[SseEnvelope]) -> TraceBody:
    """One run's journal, folded back into the shape the timeline draws.

    Built from the events rather than from the context, because by the time
    anybody asks the context is gone — and because the journal is what the
    live stream showed, so the replayed trace and the live one cannot differ.
    """
    steps: dict[int, dict[str, Any]] = {}
    calls: dict[int, list[ToolCalledPayload]] = {}
    postings: list[PostingView] = []
    trajectory: list[float] = []
    tool_calls = tokens = 0
    latency = 0.0

    for event in events:
        payload = event.payload
        if event.type == "run.started":
            prior = payload.get("prior")
            if isinstance(prior, (int, float)):
                trajectory.append(float(prior))
        elif event.type == "step.started":
            steps[int(payload.get("step", 0))] = {
                "step": int(payload.get("step", 0)),
                "name": payload.get("name", ""),
                "title": payload.get("title", ""),
                "agent": payload.get("agent", ""),
                "completed": False,
            }
        elif event.type == "step.completed":
            number = int(payload.get("step", 0))
            steps.setdefault(number, {"step": number, "name": payload.get("name", "")})
            steps[number] |= {
                "completed": True,
                "elapsed_s": float(payload.get("elapsed_s") or 0.0),
                "tool_calls_in_step": int(payload.get("tool_calls_in_step") or 0),
                "postings_in_step": int(payload.get("postings_in_step") or 0),
            }
        elif event.type == "tool.called":
            # The step belongs to the envelope. Older recorded runs did not
            # also copy it into the payload, so read the envelope and fall
            # back to the payload rather than the other way round.
            number = event.step if event.step is not None else int(payload.get("step") or 0)
            calls.setdefault(number, []).append(
                ToolCalledPayload.model_validate(payload | {"step": number})
            )
        elif event.type == "evidence.posted":
            postings.append(PostingView.model_validate(payload))
            trajectory.append(float(payload.get("p_after") or 0.0))
        elif event.type == "run.completed":
            tool_calls = int(payload.get("tool_calls") or 0)
            tokens = int(payload.get("tokens") or 0)
            latency = float(payload.get("latency_s") or 0.0)

    return TraceBody(
        case_id=case_id,
        run_id=run_id,
        steps=[
            TraceStep.model_validate(body | {"calls": calls.get(number, [])})
            for number, body in sorted(steps.items())
        ],
        postings=postings,
        trajectory=trajectory,
        tool_calls=tool_calls,
        tokens=tokens,
        latency_s=latency,
    )


def _matches(
    row: CaseSummary,
    status: CaseStatus | None,
    verdict: Verdict | None,
    trigger_type: TriggerType | None,
    q: str,
) -> bool:
    if status is not None and row.status is not status:
        return False
    if verdict is not None and row.verdict is not verdict:
        return False
    if trigger_type is not None and row.trigger_type is not trigger_type:
        return False
    if not q:
        return True
    needle = q.strip().lower()
    haystack = " ".join(
        part
        for part in (row.case_id, row.card_id, row.customer_id, row.flagged_txn_id, row.summary)
        if part
    )
    return needle in haystack.lower()


def _sort_key(sort: CaseSort) -> Any:
    if sort is CaseSort.PROBABILITY:
        return lambda row: row.fraud_probability or -1.0
    if sort is CaseSort.EXPOSURE:
        return lambda row: row.exposure_usd or -1.0
    return lambda row: row.opened_at


def _render_sar(case_id: str, answer: AnswerFile) -> str:
    """The filing as a human reads it — or the reasoned decision not to file.

    Plain text on purpose. A SAR narrative is read, not parsed, and the one
    place the brief asks for completeness is here.
    """
    sar = answer.sar
    case = answer.case
    if not sar.file:
        return (
            f"SUSPICIOUS ACTIVITY REPORT — NOT FILED\n"
            f"Case {case_id}\n\n"
            f"Decision: no report.\n"
            f"Reason:   {sar.reason or 'no reason recorded'}\n\n"
            f"Verdict {case.verdict.value} at probability {case.fraud_probability:.2f}; "
            f"exposure ${case.exposure_usd:,.2f}.\n"
        )
    dates = " to ".join(sar.activity_dates) or "not recorded"
    subjects = "\n".join(f"  - {s}" for s in sar.subjects) or "  - none recorded"
    return (
        f"SUSPICIOUS ACTIVITY REPORT\n"
        f"Case {case_id}\n\n"
        f"Filed because: {sar.reason}\n"
        f"Activity dates: {dates}\n"
        f"Total amount: ${sar.total_amount_usd:,.2f}\n"
        f"Subjects:\n{subjects}\n\n"
        f"NARRATIVE\n{sar.narrative}\n\n"
        f"Transactions ({len(case.affected_txn_ids)}): "
        f"{', '.join(case.affected_txn_ids) or 'none'}\n"
    )
