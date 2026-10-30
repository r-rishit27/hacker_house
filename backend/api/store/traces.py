"""The on-disk trace archive.

The API journals every event it emits, so a run started *here* can be replayed
from memory. But the twenty graded answer files were produced by the batch
runner, in a different process, days earlier — and that run's journal went to
`runs/<run_id>/trace/<case_id>.jsonl` rather than into this process.

Without this module the console's investigation tab is empty for every case
that already has an answer, which is all twenty of them. That reads as "the
agent did nothing", when in fact the full event-by-event record is sitting on
disk. This reads it back.

The file is the same envelope the live stream carried, one JSON object per
line, so a replayed trace and a live one fold through the identical code in
`_trace_from`. Only four fields are stored (`seq`, `type`, `step`, `payload`);
the rest of the envelope is recovered from where the file sits — the run id
from its directory, the case id from its name.

Nothing here writes. The archive is evidence of what happened, and the API has
no business editing it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from api.sse.journal import SseEnvelope

#: Written by `CaseRunner._write_trace`, which is the only producer.
TRACE_DIR = "trace"
TRACE_SUFFIX = ".jsonl"

#: A malformed line is skipped rather than failing the whole replay: a trace
#: truncated by a killed process is still worth most of what it holds.
_MAX_LINES = 20_000


@dataclass(frozen=True, slots=True)
class ArchivedTrace:
    """One case's recorded run, and which run it came from."""

    run_id: str
    case_id: str
    events: list[SseEnvelope]


class TraceArchive:
    """Reads recorded runs out of the runs directory.

    Cheap to construct and safe to hold: it stats the filesystem per call
    rather than caching, because a run finishing while the console is open
    should show up without a restart.
    """

    def __init__(self, runs_dir: Path) -> None:
        self._root = runs_dir

    def latest(
        self, case_id: str, *, fraud_probability: float | None = None
    ) -> ArchivedTrace | None:
        """The recorded run that produced the answer being served, or None.

        Picking the newest file is not good enough. The runs directory
        accumulates aborted probes — a handful of `step.started` events and a
        `run.completed` carrying nothing — and several of those are newer than
        the batch that produced the graded answers. Replaying one shows an
        investigation that stopped after three steps and found nothing, which
        is a lie about what the agent did.

        So when the caller knows what the answer says, the trace is matched to
        it: a run's `verdict.reached` carries the probability it settled on,
        and that identifies the run exactly — two batches over the same case
        agree to three decimal places and differ in the fourth. Failing a
        match, the fallback is the trace holding the most evidence, which is
        the most informative one on offer rather than merely the newest.
        """
        candidates = self._paths(case_id)
        if not candidates:
            return None
        path = self._pick(candidates, fraud_probability)
        run_id = path.parent.parent.name
        return ArchivedTrace(
            run_id=run_id,
            case_id=case_id,
            events=self._read(path, run_id=run_id, case_id=case_id),
        )

    def has(self, case_id: str) -> bool:
        """Whether a trace exists, without paying to parse one."""
        return bool(self._paths(case_id))

    # ── internals ────────────────────────────────────────────────────────────

    def _paths(self, case_id: str) -> list[Path]:
        """Every recorded trace for the case, newest first."""
        if not case_id or "/" in case_id or "\\" in case_id or case_id.startswith("."):
            # The case id reaches here from the URL. It indexes a filename, so
            # it is checked here rather than trusted from the router.
            return []
        if not self._root.is_dir():
            return []
        found = [
            path
            for path in self._root.glob(f"*/{TRACE_DIR}/{case_id}{TRACE_SUFFIX}")
            if path.is_file() and path.stat().st_size > 0
        ]
        return sorted(found, key=lambda path: path.stat().st_mtime, reverse=True)

    @classmethod
    def _pick(cls, candidates: list[Path], fraud_probability: float | None) -> Path:
        if fraud_probability is not None:
            for path in candidates:
                settled = cls._settled_probability(path)
                if settled is not None and abs(settled - fraud_probability) < 1e-9:
                    return path
        return max(candidates, key=cls._evidence_count)

    @staticmethod
    def _settled_probability(path: Path) -> float | None:
        """The probability a recorded run ended on, read from `verdict.reached`.

        Scanned as text first so that identifying a run costs one pass and no
        JSON parsing for the lines — almost all of them — that cannot match.
        """
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if '"verdict.reached"' not in line:
                    continue
                try:
                    payload = json.loads(line).get("payload") or {}
                except json.JSONDecodeError:
                    return None
                value = payload.get("fraud_probability")
                return float(value) if isinstance(value, (int, float)) else None
        return None

    @staticmethod
    def _evidence_count(path: Path) -> int:
        with path.open(encoding="utf-8") as handle:
            return sum(1 for line in handle if '"evidence.posted"' in line)

    @staticmethod
    def _read(path: Path, *, run_id: str, case_id: str) -> list[SseEnvelope]:
        events: list[SseEnvelope] = []
        with path.open(encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if index >= _MAX_LINES:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict) or "type" not in record:
                    continue
                step = record.get("step")
                events.append(
                    SseEnvelope(
                        v=1,
                        seq=int(record.get("seq", index)),
                        run_id=run_id,
                        case_id=case_id,
                        type=str(record["type"]),
                        # The writer stores no timestamp. Rather than invent
                        # one per event, every replayed event carries the time
                        # the file was written, which is true of all of them.
                        at="",
                        step=int(step) if isinstance(step, int) else None,
                        payload=record.get("payload") or {},
                    )
                )
        return events
