"""The pure half of answer validation.

``AnswerValidator`` holds no connection and makes no network call, so the same
object runs in three places: inside the orchestrator before an answer is written
to ``cases/``, in CI with no credentials at all, and behind the API when an
analyst edits an answer by hand. Everything that needs the live graph — id
existence and the exposure arithmetic — lives in
:class:`sentinel.validation.graph_check.GraphIdentityChecker`.

Findings carry a stable ``code`` and a JSON path, because "invalid submission" on
its own tells an analyst nothing about which of the 47 fields to fix.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sentinel.domain.errors import AnswerInvalid
from sentinel.validation.rules import ValidationRule, default_rules


@dataclass(frozen=True, slots=True)
class Finding:
    """One defect in one answer file, addressed by its JSON path.

    Collaborators: created by ``ValidationReport``; rendered by the CLI and by the
    API's problem detail.
    """

    code: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"

    def as_dict(self) -> dict[str, str]:
        """The wire form: the SSE validation event and the API problem detail."""
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(slots=True)
class ValidationReport:
    """What one validation pass found.

    Responsibility: collect findings and answer whether the answer may be written.
    Warnings never block — they mark things a human should look at, such as a
    summary that runs long.
    Collaborators: filled by ``ValidationRule`` instances and by
    ``GraphIdentityChecker``; read by the orchestrator and the API.
    """

    errors: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing blocking was found."""
        return not self.errors

    def add_error(self, code: str, path: str, message: str) -> None:
        self.errors.append(Finding(code=code, path=path, message=message))

    def add_warning(self, code: str, path: str, message: str) -> None:
        self.warnings.append(Finding(code=code, path=path, message=message))

    def raise_if_invalid(self, case_id: str = "") -> None:
        """Raise ``AnswerInvalid`` when any error was recorded.

        The orchestrator calls this before writing: an invalid answer is
        quarantined in ``runs/`` and ``cases/`` is left untouched.
        """
        if self.ok:
            return
        subject = f" for {case_id}" if case_id else ""
        raise AnswerInvalid(
            f"answer{subject} failed validation with {len(self.errors)} error(s)",
            case_id=case_id,
            errors=[{"code": f.code, "path": f.path, "message": f.message} for f in self.errors],
        )


def as_mapping(answer: object) -> Mapping[str, Any]:
    """Accept either a parsed answer file or an ``AnswerFile`` model.

    The orchestrator validates the model it just built; CI validates JSON off
    disk. ``model_dump(mode="json")`` is what makes the two identical — enum
    members become the exact strings the graders compare against.
    """
    dump = getattr(answer, "model_dump", None)
    if callable(dump):
        dumped = dump(mode="json")
        if isinstance(dumped, Mapping):
            return dumped
    if isinstance(answer, Mapping):
        return answer
    raise TypeError(f"expected an answer mapping or an AnswerFile, got {type(answer).__name__}")


class AnswerValidator:
    """Runs every connection-free rule over one answer file.

    Responsibility: sequence the rules and collect one report. It decides nothing
    about fraud; each rule owns its own family of checks.
    Collaborators: the ``ValidationRule`` instances injected at construction —
    ``default_rules()`` when the caller has no opinion — and ``ValidationReport``,
    which it hands to each rule in turn.
    """

    def __init__(self, rules: Sequence[ValidationRule] | None = None) -> None:
        self._rules: tuple[ValidationRule, ...] = (
            tuple(rules) if rules is not None else default_rules()
        )

    @property
    def rules(self) -> tuple[ValidationRule, ...]:
        return self._rules

    def validate(self, answer: object) -> ValidationReport:
        """Check ``answer`` and return everything wrong with it.

        A rule marked ``fatal`` that reports an error ends the pass: once ``case``
        is missing, the rules after it can only repeat that one fact.
        """
        payload = as_mapping(answer)
        report = ValidationReport()
        for rule in self._rules:
            errors_before = len(report.errors)
            rule.check(payload, report)
            if rule.fatal and len(report.errors) > errors_before:
                break
        return report
