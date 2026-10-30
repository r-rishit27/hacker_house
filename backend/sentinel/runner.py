"""The run harness: alerts in, answer files out, nothing hidden.

This is the only place in the system that writes to ``cases/``. Keeping it out
of the steps is what lets the API run an investigation without touching the
submission directory, and what lets a benchmark run be repeated against a
scratch directory before it is repeated for real.

Three properties it exists to guarantee, all of them things that cost score when
they go wrong:

**An invalid answer never lands in ``cases/``.** It is written to
``runs/<run_id>/quarantine/`` with its findings beside it. A submission
directory holding nineteen valid files and one invalid one is worse than one
holding nineteen, because the invalid file is the one a grader opens first.

**The twenty cases run in chronological order.** Case memory only compounds
forwards: HHG-019 may retrieve the `FraudCase` Sentinel wrote for HHG-003, and
must not be able to retrieve one from a case that had not happened yet. Order is
by ``opened_at``, not by case id, and the two differ.

**Every run leaves a trace.** The event journal is written next to the answer
whether the run succeeded or failed, because a run that fails at step seven is
the one whose trace is worth reading.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sentinel.agents.emitter import CollectingEmitter
from sentinel.agents.orchestrator import InvestigationOrchestrator, InvestigationResult
from sentinel.config.settings import Settings
from sentinel.domain.alert import Alert
from sentinel.domain.answer import AnswerFile
from sentinel.domain.errors import SentinelError
from sentinel.validation.graph_check import GraphIdentityChecker
from sentinel.validation.validator import ValidationReport

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """What one case produced, valid or not."""

    case_id: str
    ok: bool
    answer: AnswerFile | None
    report: ValidationReport | None
    path: Path | None
    error: str = ""
    elapsed_s: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        answer = self.answer
        return {
            "case_id": self.case_id,
            "ok": self.ok,
            "path": str(self.path) if self.path else None,
            "error": self.error,
            "elapsed_s": round(self.elapsed_s, 2),
            "verdict": answer.case.verdict.value if answer else None,
            "fraud_probability": answer.case.fraud_probability if answer else None,
            "pattern": answer.case.pattern.value if answer else None,
            "actions": [r.action.value for r in answer.next_best_actions.final] if answer else [],
            "sar": answer.sar.file if answer else None,
            "tool_calls": answer.tool_calls if answer else 0,
            "tokens": answer.tokens if answer else 0,
            "errors": [f.as_dict() for f in self.report.errors] if self.report else [],
            "warnings": [f.as_dict() for f in self.report.warnings] if self.report else [],
        }


@dataclass(frozen=True, slots=True)
class BatchReport:
    """The whole run, and the four ratios that say whether it is defensible.

    The thresholds are not arbitrary. Guide.md's own warning is that the failure
    mode of an over-eager agent is blocking everything, and half the benchmark
    is legitimate activity; a block rate over 0.50 or a SAR rate over 0.20 is
    the shape of a run that has stopped discriminating.
    """

    run_id: str
    outcomes: tuple[CaseOutcome, ...]
    started_at: float
    elapsed_s: float

    @property
    def valid(self) -> tuple[CaseOutcome, ...]:
        return tuple(o for o in self.outcomes if o.ok and o.answer is not None)

    @property
    def block_rate(self) -> float:
        return self._rate(
            lambda a: any(r.action.value.startswith("BLOCK_") for r in a.next_best_actions.final)
        )

    @property
    def sar_rate(self) -> float:
        return self._rate(lambda a: a.sar.file)

    @property
    def changed_rate(self) -> float:
        """Share of cases where asking for evidence actually moved the answer."""
        return self._rate(lambda a: a.next_best_actions.final != a.next_best_actions.initial)

    @property
    def verdict_mix(self) -> dict[str, int]:
        mix: dict[str, int] = {}
        for outcome in self.valid:
            assert outcome.answer is not None
            key = outcome.answer.case.verdict.value
            mix[key] = mix.get(key, 0) + 1
        return mix

    def warnings(self) -> list[str]:
        """Everything about this batch a human should look at before shipping."""
        out: list[str] = []
        n = len(self.valid)
        if n == 0:
            return ["no valid answers in this run"]
        if self.block_rate > 0.50:
            out.append(f"block rate {self.block_rate:.0%} exceeds the 50% ceiling")
        if self.sar_rate > 0.20:
            out.append(f"SAR rate {self.sar_rate:.0%} exceeds the 20% ceiling")
        dominant = max(self.verdict_mix.values()) / n if self.verdict_mix else 0.0
        if dominant > 0.60:
            out.append(f"one verdict covers {dominant:.0%} of the pack; the mix is degenerate")
        if self.changed_rate * n < 6 and n >= 20:
            out.append(
                f"only {int(self.changed_rate * n)} cases changed after evidence; "
                "the brief expects the recommendation to move"
            )
        failed = [o.case_id for o in self.outcomes if not o.ok]
        if failed:
            out.append(f"{len(failed)} case(s) did not produce a valid answer: {', '.join(failed)}")
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "cases": len(self.outcomes),
            "valid": len(self.valid),
            "elapsed_s": round(self.elapsed_s, 2),
            "block_rate": round(self.block_rate, 4),
            "sar_rate": round(self.sar_rate, 4),
            "changed_rate": round(self.changed_rate, 4),
            "verdict_mix": self.verdict_mix,
            "tokens": sum(o.answer.tokens for o in self.valid if o.answer),
            "tool_calls": sum(o.answer.tool_calls for o in self.valid if o.answer),
            "warnings": self.warnings(),
            "outcomes": [o.as_dict() for o in self.outcomes],
        }

    def _rate(self, predicate: Any) -> float:
        valid = self.valid
        if not valid:
            return 0.0
        return sum(1 for o in valid if predicate(o.answer)) / len(valid)


@dataclass
class CaseRunner:
    """Runs alerts through an orchestrator and lands the results on disk.

    Responsibility: ordering, file placement, quarantine and the batch report.
    It makes no fraud decision and does not know what a step is.
    Collaborators: an ``InvestigationOrchestrator`` per case; a
    ``GraphIdentityChecker`` when ids are to be proved against the live graph.
    """

    settings: Settings
    orchestrator: InvestigationOrchestrator
    run_id: str
    checker: GraphIdentityChecker | None = None
    #: Where valid answers land. Overridden by a scratch run.
    cases_dir: Path | None = None
    _seen: list[str] = field(default_factory=list)

    @property
    def out_dir(self) -> Path:
        return self.cases_dir or self.settings.cases_dir

    @property
    def run_dir(self) -> Path:
        return self.settings.runs_dir / self.run_id

    async def run_one(self, alert: Alert) -> CaseOutcome:
        """One case, from alert to a file on disk."""
        started = time.perf_counter()
        emitter = CollectingEmitter()
        logger.info("── %s ── %s", alert.alert_id, alert.trigger_type.value)
        try:
            result = await self.orchestrator.run(alert, emitter)
        except SentinelError as exc:
            self._write_trace(alert.alert_id, emitter)
            logger.error("%s failed: %s", alert.alert_id, exc.message)
            return CaseOutcome(
                case_id=alert.alert_id,
                ok=False,
                answer=None,
                report=None,
                path=None,
                error=f"{exc.code}: {exc.message}",
                elapsed_s=time.perf_counter() - started,
            )

        report = result.validation
        if self.checker is not None:
            # Graph identity is checked here rather than inside the orchestrator:
            # it is the one validation that needs a network call, and a run
            # against the fake must not be forced to make one.
            await self.checker.check(result.answer.model_dump(mode="json"), report)

        self._write_trace(alert.alert_id, emitter)
        path = self._write_answer(result.answer, report)
        elapsed = time.perf_counter() - started
        self._log_outcome(alert.alert_id, result, report, elapsed)
        return CaseOutcome(
            case_id=alert.alert_id,
            ok=report.ok,
            answer=result.answer,
            report=report,
            path=path,
            elapsed_s=elapsed,
        )

    async def run_batch(self, alerts: Sequence[Alert]) -> BatchReport:
        """Every alert, oldest first, one at a time.

        Serial on purpose. Case memory only compounds if case nineteen runs
        after case three has been written, and a concurrent batch would also
        make the token and latency numbers in each answer file meaningless.
        """
        ordered = sorted(alerts, key=lambda a: (a.opened_at, a.alert_id))
        started = time.perf_counter()
        outcomes: list[CaseOutcome] = []
        for index, alert in enumerate(ordered, start=1):
            logger.info(
                "[%d/%d] %s opened %s", index, len(ordered), alert.alert_id, alert.opened_at
            )
            outcomes.append(await self.run_one(alert))
        report = BatchReport(
            run_id=self.run_id,
            outcomes=tuple(outcomes),
            started_at=started,
            elapsed_s=time.perf_counter() - started,
        )
        self._write_report(report)
        return report

    # ── disk ─────────────────────────────────────────────────────────────────

    def _write_answer(self, answer: AnswerFile, report: ValidationReport) -> Path:
        """A valid answer goes to ``cases/``; an invalid one to quarantine."""
        if report.ok:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            path = self.out_dir / f"{answer.case_id}.json"
        else:
            quarantine = self.run_dir / "quarantine"
            quarantine.mkdir(parents=True, exist_ok=True)
            path = quarantine / f"{answer.case_id}.json"
            (quarantine / f"{answer.case_id}.findings.json").write_text(
                json.dumps(
                    {
                        "errors": [f.as_dict() for f in report.errors],
                        "warnings": [f.as_dict() for f in report.warnings],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            logger.error(
                "%s quarantined with %d error(s): %s",
                answer.case_id,
                len(report.errors),
                "; ".join(str(f) for f in report.errors[:3]),
            )
        path.write_text(answer.to_json() + "\n", encoding="utf-8")
        return path

    def _write_trace(self, case_id: str, emitter: CollectingEmitter) -> None:
        """The event journal, one JSON object per line, replayable."""
        traces = self.run_dir / "trace"
        traces.mkdir(parents=True, exist_ok=True)
        with (traces / f"{case_id}.jsonl").open("w", encoding="utf-8") as handle:
            for index, event in enumerate(emitter.events):
                handle.write(
                    json.dumps(
                        {
                            "seq": index,
                            "type": event.type,
                            "step": event.step,
                            "payload": event.payload,
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n"
                )

    def _write_report(self, report: BatchReport) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "report.json").write_text(
            json.dumps(report.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @staticmethod
    def _log_outcome(
        case_id: str, result: InvestigationResult, report: ValidationReport, elapsed: float
    ) -> None:
        answer = result.answer
        logger.info(
            "%s  verdict=%s p=%.4f pattern=%s actions=%s sar=%s "
            "tool_calls=%d tokens=%d in %.1fs valid=%s",
            case_id,
            answer.case.verdict.value,
            answer.case.fraud_probability,
            answer.case.pattern.value,
            ",".join(r.action.value for r in answer.next_best_actions.final) or "none",
            answer.sar.file,
            answer.tool_calls,
            answer.tokens,
            elapsed,
            report.ok,
        )


def new_run_id(now: float | None = None) -> str:
    """``run-20260921-110431``. Sortable, and unique enough for one machine."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now if now is not None else time.time()))
    return f"run-{stamp}"
