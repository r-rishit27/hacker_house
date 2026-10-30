"""The archive must pick the run that produced the answer being served.

The runs directory accumulates aborted probes — a handful of `step.started`
events and a `run.completed` carrying nothing — and several of those are
*newer* than the batch that produced the graded answers. Replaying one shows
an investigation that stopped after three steps and found nothing, which is a
lie about what the agent did. That is the defect these tests pin.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from api.store.traces import TraceArchive


def _write(root: Path, run_id: str, case_id: str, events: list[dict[str, object]]) -> Path:
    directory = root / run_id / "trace"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{case_id}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for index, event in enumerate(events):
            handle.write(json.dumps({"seq": index, **event}) + "\n")
    return path


def _run(probability: float, postings: int) -> list[dict[str, object]]:
    events: list[dict[str, object]] = [
        {"type": "run.started", "step": None, "payload": {"prior": 0.25}}
    ]
    events += [
        {"type": "evidence.posted", "step": 1, "payload": {"p_after": probability}}
        for _ in range(postings)
    ]
    events.append(
        {"type": "verdict.reached", "step": 9, "payload": {"fraud_probability": probability}}
    )
    events.append({"type": "run.completed", "step": None, "payload": {"tokens": 100}})
    return events


def _touch(path: Path, when: float) -> None:
    os.utime(path, (when, when))


def test_missing_case_returns_none(tmp_path: Path) -> None:
    assert TraceArchive(tmp_path).latest("HHG-001") is None
    assert TraceArchive(tmp_path).has("HHG-001") is False


def test_prefers_the_run_matching_the_answer(tmp_path: Path) -> None:
    """A newer aborted run must not beat the batch that wrote the answer."""
    good = _write(tmp_path, "submission-06", "HHG-004", _run(0.9911, postings=15))
    stale = _write(tmp_path, "submission-01", "HHG-004", _run(0.9921, postings=15))
    aborted = _write(tmp_path, "probe-99", "HHG-004", _run(0.0, postings=0))
    _touch(good, 1_000)
    _touch(stale, 900)
    _touch(aborted, 9_999)  # newest by a mile, and worthless

    picked = TraceArchive(tmp_path).latest("HHG-004", fraud_probability=0.9911)
    assert picked is not None
    assert picked.run_id == "submission-06"
    assert sum(1 for event in picked.events if event.type == "evidence.posted") == 15


def test_falls_back_to_the_most_evidential_run(tmp_path: Path) -> None:
    """With no answer to match against, take the trace that holds the most."""
    thin = _write(tmp_path, "probe-99", "HHG-004", _run(0.5, postings=1))
    fat = _write(tmp_path, "submission-06", "HHG-004", _run(0.5, postings=12))
    _touch(thin, 9_999)
    _touch(fat, 100)

    picked = TraceArchive(tmp_path).latest("HHG-004")
    assert picked is not None
    assert picked.run_id == "submission-06"


def test_unmatched_probability_still_returns_the_best_available(tmp_path: Path) -> None:
    _write(tmp_path, "submission-06", "HHG-004", _run(0.42, postings=7))
    picked = TraceArchive(tmp_path).latest("HHG-004", fraud_probability=0.99)
    assert picked is not None
    assert picked.run_id == "submission-06"


def test_step_is_recovered_from_the_envelope(tmp_path: Path) -> None:
    """Older runs stored the step on the envelope and not in the payload."""
    _write(
        tmp_path,
        "old-run",
        "HHG-002",
        [{"type": "tool.called", "step": 4, "payload": {"tool": "txn_detail", "ok": True}}],
    )
    picked = TraceArchive(tmp_path).latest("HHG-002")
    assert picked is not None
    assert picked.events[0].step == 4


def test_malformed_lines_are_skipped_not_fatal(tmp_path: Path) -> None:
    directory = tmp_path / "run-1" / "trace"
    directory.mkdir(parents=True)
    (directory / "HHG-003.jsonl").write_text(
        '{"seq": 0, "type": "run.started", "payload": {}}\n'
        "not json at all\n"
        "\n"
        '{"no_type": true}\n'
        '{"seq": 3, "type": "run.completed", "payload": {}}\n',
        encoding="utf-8",
    )
    picked = TraceArchive(tmp_path).latest("HHG-003")
    assert picked is not None
    assert [event.type for event in picked.events] == ["run.started", "run.completed"]


def test_a_case_id_cannot_escape_the_runs_directory(tmp_path: Path) -> None:
    """The id arrives from a URL and indexes a filename."""
    archive = TraceArchive(tmp_path)
    for hostile in ("../secret", "..", ".hidden", "a/b", "a\\b", ""):
        assert archive.latest(hostile) is None
        assert archive.has(hostile) is False


def test_empty_files_are_ignored(tmp_path: Path) -> None:
    directory = tmp_path / "run-1" / "trace"
    directory.mkdir(parents=True)
    (directory / "HHG-005.jsonl").write_text("", encoding="utf-8")
    assert TraceArchive(tmp_path).has("HHG-005") is False
