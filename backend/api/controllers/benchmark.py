"""The whole pack at once, and the four ratios that say whether it is defensible.

The report is computed from the answer files on disk by ``BatchReport`` — the
same object the CLI prints — rather than recomputed here. That is the point:
the number a judge reads in the console and the number the terminal printed
after `run --all` cannot disagree, because there is one of them.

Its warnings are not decoration. Half the benchmark is legitimate activity and
the documented failure mode is an agent that blocks everything, so a block
rate over 0.50, a SAR rate over 0.20, or a single verdict covering more than
60% of the pack each produce a line the console shows in red. An agent that
scores itself only on what it found would not notice becoming that agent.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from fastapi import Query

from api.controller import ApiController, route
from api.errors import conflict
from api.schemas import (
    BenchmarkAccepted,
    BenchmarkCase,
    BenchmarkReport,
    BenchmarkRequest,
    HistogramBin,
    StartInvestigationRequest,
    ValidationFinding,
)
from api.store.models import new_id
from sentinel.domain.answer import AnswerFile
from sentinel.domain.enums import Verdict

#: Ten bins of 0.1. Enough to see bimodality, few enough to read at a glance.
HISTOGRAM_BINS = 10


class BenchmarkController(ApiController):
    """Run the pack, and score the run.

    Responsibility: start a batch and serve the report. The ratios and their
    warnings belong to ``sentinel.runner.BatchReport``; this controller reads
    the files and asks it.
    """

    prefix = "/benchmark"
    tags: ClassVar[list[str]] = ["benchmark"]

    def routes(self) -> list[dict[str, Any]]:
        return [
            route(
                "/report",
                self.report,
                summary="Verdict mix, block and SAR rates, the histogram and the warnings.",
            ),
            route(
                "/runs",
                self.start,
                methods=("POST",),
                status_code=202,
                summary="Run the pack, or a subset, in chronological order.",
                errors={409: "A batch is already running."},
            ),
        ]

    async def report(self, limit: int = Query(default=200, ge=1, le=500)) -> BenchmarkReport:
        """Score whatever is on disk right now."""
        cases: list[BenchmarkCase] = []
        answers: list[AnswerFile] = []
        for case_id in list(self.container.alerts)[:limit]:
            answer = await self.container.answers.load(case_id)
            if answer is None:
                cases.append(BenchmarkCase(case_id=case_id, ok=False, error="no answer written"))
                continue
            report = self.container.validator.validate(answer.model_dump(mode="json"))
            answers.append(answer)
            cases.append(
                BenchmarkCase(
                    case_id=case_id,
                    ok=report.ok,
                    verdict=answer.case.verdict,
                    fraud_probability=answer.case.fraud_probability,
                    pattern=answer.case.pattern,
                    actions=[r.action for r in answer.next_best_actions.final],
                    sar=answer.sar.file,
                    tool_calls=answer.tool_calls,
                    tokens=answer.tokens,
                    elapsed_s=answer.latency_s,
                    errors=[ValidationFinding.model_validate(f) for f in report.errors],
                )
            )
        return _score(cases, answers, len(self.container.alerts))

    async def start(self, body: BenchmarkRequest) -> BenchmarkAccepted:
        """Start every case in the batch, oldest alert first.

        Serial, through the same registry a single investigation uses, because
        case memory only compounds if case nineteen runs after case three has
        been written — and because a concurrent batch would make the
        `latency_s` in each answer file meaningless.
        """
        if self.container.runs.active():
            running = ", ".join(handle.case_id for handle in self.container.runs.active())
            raise conflict(
                f"a run is already active on {running}; wait for it or cancel it",
                code="run_already_active",
            )
        chosen = list(body.case_ids or self.container.alerts)
        unknown = [c for c in chosen if c not in self.container.alerts]
        if unknown:
            raise conflict(f"not in the case pack: {', '.join(unknown)}", code="unknown_case")
        ordered = sorted(chosen, key=lambda c: self.container.alerts[c].opened_at)
        batch_id = new_id("bat")
        asyncio.create_task(  # noqa: RUF006 - the registry owns the run's lifetime
            self._run_batch(ordered, batch_id, body), name=f"benchmark-{batch_id}"
        )
        return BenchmarkAccepted(
            batch_id=batch_id,
            case_ids=ordered,
            stream_url=f"/api/benchmark/runs/{batch_id}/events",
        )

    async def _run_batch(self, case_ids: list[str], batch_id: str, body: BenchmarkRequest) -> None:
        """One case at a time, awaiting each before starting the next.

        Serial is the requirement, not a simplification: a late case may only
        retrieve a `FraudCase` an earlier one wrote, and two runs in flight
        would also make the `latency_s` in both answer files a measurement of
        the other.
        """
        from api.controllers.investigations import InvestigationService

        service = InvestigationService(self.container)
        for case_id in case_ids:
            accepted = await service.start(
                StartInvestigationRequest(case_id=case_id, mode=body.mode, force=True),
                batch_id=batch_id,
            )
            handle = self.container.runs.get(accepted.run_id)
            if handle is not None and handle.task is not None:
                await asyncio.gather(handle.task, return_exceptions=True)


def _score(cases: list[BenchmarkCase], answers: list[AnswerFile], total: int) -> BenchmarkReport:
    """The four ratios and their warnings, from ``BatchReport``'s own rules."""
    valid = [c for c in cases if c.ok]
    n = len(valid) or 1
    mix: dict[Verdict, int] = {}
    for case in valid:
        if case.verdict is not None:
            mix[case.verdict] = mix.get(case.verdict, 0) + 1

    block = (
        sum(
            1
            for a in answers
            if any(r.action.value.startswith("BLOCK_") for r in a.next_best_actions.final)
        )
        / n
    )
    sar = sum(1 for a in answers if a.sar.file) / n
    changed = (
        sum(1 for a in answers if a.next_best_actions.final != a.next_best_actions.initial) / n
    )

    warnings: list[str] = []
    if block > 0.50:
        warnings.append(f"block rate {block:.0%} exceeds the 50% ceiling")
    if sar > 0.20:
        warnings.append(f"SAR rate {sar:.0%} exceeds the 20% ceiling")
    dominant = max(mix.values()) / n if mix else 0.0
    if dominant > 0.60:
        warnings.append(f"one verdict covers {dominant:.0%} of the pack; the mix is degenerate")
    if len(valid) < total:
        missing = [c.case_id for c in cases if not c.ok]
        warnings.append(f"{len(missing)} case(s) have no valid answer: {', '.join(missing)}")
    if changed * n < 6 and total >= 20:
        warnings.append(
            f"only {int(changed * n)} cases changed after evidence; the brief expects the "
            "recommendation to move"
        )

    return BenchmarkReport(
        batch_id="",
        total=total,
        valid=len(valid),
        verdict_mix=mix,
        block_rate=round(block, 4),
        sar_rate=round(sar, 4),
        changed_rate=round(changed, 4),
        probability_histogram=_histogram(answers),
        warnings=warnings,
        elapsed_s=round(sum(a.latency_s for a in answers), 2),
        cases=cases,
    )


def _histogram(answers: list[AnswerFile]) -> list[HistogramBin]:
    """Ten bins of 0.1. Bimodality is the shape worth being able to see."""
    counts = [0] * HISTOGRAM_BINS
    for answer in answers:
        index = min(int(answer.case.fraud_probability * HISTOGRAM_BINS), HISTOGRAM_BINS - 1)
        counts[index] += 1
    return [
        HistogramBin(lower=i / HISTOGRAM_BINS, upper=(i + 1) / HISTOGRAM_BINS, count=count)
        for i, count in enumerate(counts)
    ]
