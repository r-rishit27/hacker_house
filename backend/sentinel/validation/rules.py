"""Every check on an answer file that can be made without a database connection.

Ported from ``eval/validate.py``. Four checks here are new, each because Guide.md
requires something the old validator never looked at:

* ``sar.reason`` — REQUIRED in both the filing and the not-filing branch.
* ``next_best_actions.what_changed`` — never inspected beyond its presence.
* ``case.summary`` — Guide.md bounds it at two to six sentences.
* R10 — v1 passed ``BLOCK_ALL_CARDS`` the moment one connected card was listed,
  with no regard to the verdict. The policy requires two of the customer's cards
  to show *confirmed* fraud, or credentials *confirmed* compromised, so the weak
  check let a generated answer satisfy the validator while breaking the policy.

Every vocabulary is read from ``domain.enums``; nothing is transcribed a second
time, which is how v1 ended up with the 14 action names in two files.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from sentinel.domain.enums import (
    FIXED_ROUTES,
    Action,
    CaseStatus,
    EvidenceSource,
    Pattern,
    RequestType,
    Route,
    Verdict,
)

if TYPE_CHECKING:
    from sentinel.validation.validator import ValidationReport

# ── The contractual field lists. Guide.md: "Missing fields score zero for that part." ──

#: 9 top-level fields.
TOP_LEVEL_FIELDS: tuple[str, ...] = (
    "case_id",
    "case",
    "evidence_requests",
    "next_best_actions",
    "sar",
    "stop_reason",
    "tool_calls",
    "tokens",
    "latency_s",
)
#: 15 fields in ``case``.
CASE_FIELDS: tuple[str, ...] = (
    "status",
    "verdict",
    "fraud_probability",
    "pattern",
    "pattern_description",
    "affected_txn_ids",
    "first_suspicious_txn_id",
    "connected_card_ids",
    "connected_device_profiles",
    "exposure_usd",
    "evidence",
    "similar_prior_cases",
    "summary",
    "written_to_graph",
    "graph_case_id",
)
#: 4 fields in each ``case.evidence[]``.
EVIDENCE_FIELDS: tuple[str, ...] = ("claim", "source", "ref", "entity_ids")
#: 3 fields in each ``evidence_requests[]``.
REQUEST_FIELDS: tuple[str, ...] = ("type", "asked_after_step", "assumed_response")
#: 3 fields in ``next_best_actions``.
ACTIONS_FIELDS: tuple[str, ...] = ("initial", "final", "what_changed")
#: 6 fields in ``sar``.
SAR_FIELDS: tuple[str, ...] = (
    "file",
    "reason",
    "narrative",
    "subjects",
    "total_amount_usd",
    "activity_dates",
)
#: 3 fields in each ``next_best_actions.*[]``.
RECOMMENDATION_FIELDS: tuple[str, ...] = ("action", "route", "reason")

ACTION_VALUES: frozenset[str] = frozenset(a.value for a in Action)
ROUTE_VALUES: frozenset[str] = frozenset(r.value for r in Route)
STATUS_VALUES: frozenset[str] = frozenset(s.value for s in CaseStatus)
VERDICT_VALUES: frozenset[str] = frozenset(v.value for v in Verdict)
PATTERN_VALUES: frozenset[str] = frozenset(p.value for p in Pattern)
SOURCE_VALUES: frozenset[str] = frozenset(s.value for s in EvidenceSource)
REQUEST_TYPE_VALUES: frozenset[str] = frozenset(t.value for t in RequestType)

#: Guide.md: two to six sentences an analyst could read.
SUMMARY_SENTENCES: tuple[int, int] = (2, 6)
#: Guide.md: six to twelve sentences. This is what a regulator reads.
NARRATIVE_SENTENCES: tuple[int, int] = (6, 12)

_CASE_ID = re.compile(r"^HHG-\d{3}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EVIDENCE_REQUEST_REF = re.compile(r"^evidence_request:(\d+)$")
_DECIMAL = re.compile(r"\d+\.\d+")
_SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)")


def count_sentences(text: str) -> int:
    """Count sentence terminators, with decimal points masked first.

    v1 used ``len(text.split("."))``. Every narrative in this benchmark quotes
    dollar amounts and probabilities, so "$49.00" counted as two sentences and the
    length check it fed was meaningless.
    """
    if not text.strip():
        return 0
    masked = _DECIMAL.sub("0", text)
    # Prose with no terminator at all is still one sentence, not zero.
    return len(_SENTENCE_END.findall(masked)) or 1


def is_number(value: object) -> bool:
    """True for a JSON number. ``bool`` is an ``int`` in Python and is not one."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def is_str_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


class ValidationRule(ABC):
    """One family of checks over a parsed answer file.

    Responsibility: inspect the answer and record what is wrong with it. A rule
    never raises on bad input — malformed input is precisely what it exists to
    describe — so every accessor is defensive.
    Collaborators: ``AnswerValidator`` runs it; ``ValidationReport`` collects the
    findings.
    """

    #: Stable identifier for this family of checks.
    code: str = "rule"
    #: When true, a failure here stops the pass: the rules after it would only
    #: restate the same structural problem against fields that are not there.
    fatal: bool = False

    @abstractmethod
    def check(self, answer: Mapping[str, Any], report: ValidationReport) -> None:
        """Record every finding this rule sees in ``answer``."""


class ShapeRule(ValidationRule):
    """Field presence, JSON types, closed vocabularies and field-level bounds.

    Responsibility: the contractual field counts (9 / 15 / 4 / 3 / 3 / 6 / 3), the
    enum vocabularies from ``domain.enums``, the 0–1 probability range, the
    ``undocumented`` ⇔ ``pattern_description`` pair, and the sentence bound
    Guide.md puts on ``case.summary`` — which v1 never checked.
    Collaborators: ``ValidationReport``. Marked ``fatal`` because a missing
    ``case`` or ``sar`` makes every later rule report the same hole again.
    """

    code = "shape"
    fatal = True

    def check(self, answer: Mapping[str, Any], report: ValidationReport) -> None:
        errors_before = len(report.errors)
        for name in TOP_LEVEL_FIELDS:
            if name not in answer:
                report.add_error("missing_field", name, f"missing top-level field '{name}'")
        if len(report.errors) > errors_before:
            return

        self._check_case_id(answer, report)
        self._check_run_metrics(answer, report)

        case = answer.get("case")
        if not isinstance(case, Mapping):
            report.add_error("wrong_type", "case", "case must be an object")
            return
        self._check_case(case, report)
        self._check_evidence(case, report)
        self._check_containers(answer, report)

    def _check_case_id(self, answer: Mapping[str, Any], report: ValidationReport) -> None:
        case_id = answer.get("case_id")
        if not nonempty_str(case_id):
            report.add_error("empty_field", "case_id", "case_id is required")
        elif not _CASE_ID.match(str(case_id)):
            report.add_warning(
                "unexpected_case_id",
                "case_id",
                f"case_id '{case_id}' is not of the form HHG-NNN used by case_pack.csv",
            )

    def _check_run_metrics(self, answer: Mapping[str, Any], report: ValidationReport) -> None:
        if not nonempty_str(answer.get("stop_reason")):
            report.add_error(
                "empty_field",
                "stop_reason",
                "stop_reason is required: why the investigation ended here",
            )
        for name in ("tool_calls", "tokens"):
            value = answer.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                report.add_error(
                    "wrong_type", name, f"{name} must be a non-negative integer, got {value!r}"
                )
        latency = answer.get("latency_s")
        if not is_number(latency) or float(str(latency)) < 0:
            report.add_error(
                "wrong_type",
                "latency_s",
                f"latency_s must be a non-negative number, got {latency!r}",
            )

    def _check_case(self, case: Mapping[str, Any], report: ValidationReport) -> None:
        for name in CASE_FIELDS:
            if name not in case:
                report.add_error("missing_field", f"case.{name}", f"case: missing field '{name}'")

        self._check_enum(case.get("status"), STATUS_VALUES, "case.status", report)
        self._check_enum(case.get("verdict"), VERDICT_VALUES, "case.verdict", report)
        self._check_enum(case.get("pattern"), PATTERN_VALUES, "case.pattern", report)

        probability = case.get("fraud_probability")
        if not is_number(probability) or not 0.0 <= float(probability) <= 1.0:  # type: ignore[arg-type]
            report.add_error(
                "out_of_range",
                "case.fraud_probability",
                f"fraud_probability {probability!r} is not a number in 0..1",
            )

        description = case.get("pattern_description")
        if case.get("pattern") == Pattern.UNDOCUMENTED.value and not nonempty_str(description):
            report.add_error(
                "empty_field",
                "case.pattern_description",
                "pattern is 'undocumented' but pattern_description is empty",
            )
        if case.get("pattern") != Pattern.UNDOCUMENTED.value and nonempty_str(description):
            report.add_warning(
                "unexpected_value",
                "case.pattern_description",
                "pattern_description is set but pattern is not 'undocumented'",
            )

        for name in (
            "affected_txn_ids",
            "connected_card_ids",
            "connected_device_profiles",
            "similar_prior_cases",
        ):
            if name in case and not is_str_list(case.get(name)):
                report.add_error("wrong_type", f"case.{name}", f"{name} must be a list of strings")
        for name in ("first_suspicious_txn_id", "graph_case_id"):
            if name in case and not isinstance(case.get(name), str):
                report.add_error(
                    "wrong_type", f"case.{name}", f'{name} must be a string ("" when absent)'
                )
        if "written_to_graph" in case and not isinstance(case.get("written_to_graph"), bool):
            report.add_error(
                "wrong_type", "case.written_to_graph", "written_to_graph must be a boolean"
            )

        exposure = case.get("exposure_usd")
        if not is_number(exposure) or float(exposure) < 0:  # type: ignore[arg-type]
            report.add_error(
                "wrong_type",
                "case.exposure_usd",
                f"exposure_usd must be a non-negative number, got {exposure!r}",
            )

        self._check_summary(case, report)

    def _check_summary(self, case: Mapping[str, Any], report: ValidationReport) -> None:
        summary = case.get("summary")
        if not nonempty_str(summary):
            report.add_error(
                "empty_field", "case.summary", "summary is required: two to six sentences"
            )
            return
        low, high = SUMMARY_SENTENCES
        sentences = count_sentences(str(summary))
        if sentences < low:
            report.add_error(
                "summary_too_short",
                "case.summary",
                f"summary reads as {sentences} sentence(s); Guide.md asks for {low} to {high}",
            )
        elif sentences > high:
            # A warning, not an error: the sentence counter is an approximation and
            # an over-long summary costs style marks, not the field.
            report.add_warning(
                "summary_too_long",
                "case.summary",
                f"summary reads as {sentences} sentences; Guide.md asks for {low} to {high}",
            )

    def _check_evidence(self, case: Mapping[str, Any], report: ValidationReport) -> None:
        evidence = case.get("evidence")
        if not isinstance(evidence, list):
            report.add_error("wrong_type", "case.evidence", "evidence must be a list")
            return
        if not evidence:
            report.add_error("empty_field", "case.evidence", "case.evidence is empty")
        for index, item in enumerate(evidence):
            path = f"case.evidence[{index}]"
            if not isinstance(item, Mapping):
                report.add_error("wrong_type", path, "evidence entry must be an object")
                continue
            for name in EVIDENCE_FIELDS:
                if name not in item:
                    report.add_error("missing_field", f"{path}.{name}", f"{path}: missing '{name}'")
            self._check_enum(item.get("source"), SOURCE_VALUES, f"{path}.source", report)
            for name in ("claim", "ref"):
                if name in item and not nonempty_str(item.get(name)):
                    report.add_error("empty_field", f"{path}.{name}", f"{path}.{name} is empty")
            if "entity_ids" in item and not is_str_list(item.get("entity_ids")):
                report.add_error(
                    "wrong_type", f"{path}.entity_ids", "entity_ids must be a list of strings"
                )

    def _check_containers(self, answer: Mapping[str, Any], report: ValidationReport) -> None:
        """Presence only. The value checks belong to the rule that owns each block."""
        if not isinstance(answer.get("evidence_requests"), list):
            report.add_error("wrong_type", "evidence_requests", "evidence_requests must be a list")
        else:
            for index, request in enumerate(answer["evidence_requests"]):
                path = f"evidence_requests[{index}]"
                if not isinstance(request, Mapping):
                    report.add_error("wrong_type", path, "evidence request must be an object")
                    continue
                for name in REQUEST_FIELDS:
                    if name not in request:
                        report.add_error(
                            "missing_field", f"{path}.{name}", f"{path}: missing '{name}'"
                        )

        for block, fields in (("next_best_actions", ACTIONS_FIELDS), ("sar", SAR_FIELDS)):
            value = answer.get(block)
            if not isinstance(value, Mapping):
                report.add_error("wrong_type", block, f"{block} must be an object")
                continue
            for name in fields:
                if name not in value:
                    report.add_error(
                        "missing_field", f"{block}.{name}", f"{block}: missing '{name}'"
                    )

    def _check_enum(
        self, value: object, allowed: frozenset[str], path: str, report: ValidationReport
    ) -> None:
        if value not in allowed:
            report.add_error("unknown_value", path, f"{path} {value!r} not in {sorted(allowed)}")


class RoutingRule(ValidationRule):
    """Action names, approval routes, action reasons, ``what_changed`` and the R10 bar.

    Responsibility: everything inside ``next_best_actions`` except its relationship
    to the evidence requests. Routes are checked against ``FIXED_ROUTES``;
    ``BLOCK_CARD`` is the single action in the policy whose route depends on
    exposure, which is why it is absent from that table.
    Collaborators: ``ValidationReport``; ``domain.enums`` supplies the vocabulary.
    """

    code = "routing"

    def __init__(self, block_card_l2_threshold: float = 2_500.0) -> None:
        # Mirrors PolicyConfig.block_card_l2_threshold, injected rather than imported:
        # this rule grades the policy engine's output, so borrowing the engine's own
        # constant would hide a drift in exactly the place that matters. The boundary
        # is inclusive of L1 — $2,500.00 routes L1, $2,500.01 routes L2.
        self._block_card_l2_threshold = block_card_l2_threshold

    def check(self, answer: Mapping[str, Any], report: ValidationReport) -> None:
        actions = answer.get("next_best_actions")
        case = answer.get("case")
        if not isinstance(actions, Mapping) or not isinstance(case, Mapping):
            return

        if not nonempty_str(actions.get("what_changed")):
            report.add_error(
                "empty_field",
                "next_best_actions.what_changed",
                "what_changed is required: one or two sentences on why final differs from initial, or 'nothing'",
            )

        exposure = case.get("exposure_usd")
        exposure_usd = float(exposure) if is_number(exposure) else 0.0  # type: ignore[arg-type]
        for phase in ("initial", "final"):
            self._check_phase(actions.get(phase), phase, exposure_usd, report)
        self._check_r10(case, actions, report)

    def _check_phase(
        self, items: object, phase: str, exposure_usd: float, report: ValidationReport
    ) -> None:
        if not isinstance(items, list):
            report.add_error("wrong_type", f"next_best_actions.{phase}", f"{phase} must be a list")
            return
        if not items:
            report.add_error(
                "actions_empty",
                f"next_best_actions.{phase}",
                f"{phase} must recommend at least one action",
            )
        seen: set[str] = set()
        for index, item in enumerate(items):
            path = f"next_best_actions.{phase}[{index}]"
            if not isinstance(item, Mapping):
                report.add_error("wrong_type", path, "action entry must be an object")
                continue
            for name in RECOMMENDATION_FIELDS:
                if name not in item:
                    report.add_error("missing_field", f"{path}.{name}", f"{path}: missing '{name}'")

            name_value, route = item.get("action"), item.get("route")
            if name_value not in ACTION_VALUES:
                report.add_error(
                    "unknown_action",
                    f"{path}.action",
                    f"'{name_value}' is not one of the 14 policy actions",
                )
                continue
            action_name = str(name_value)
            if route not in ROUTE_VALUES:
                report.add_error(
                    "unknown_route",
                    f"{path}.route",
                    f"route {route!r} not in {sorted(ROUTE_VALUES)}",
                )
            else:
                expected = self._expected_route(action_name, exposure_usd)
                if route != expected:
                    report.add_error(
                        "route_mismatch",
                        f"{path}.route",
                        f"{action_name}{self._exposure_clause(action_name, exposure_usd)} must route "
                        f"'{expected}', got '{route}'",
                    )
            if not nonempty_str(item.get("reason")):
                report.add_error("empty_field", f"{path}.reason", f"{action_name} has no reason")
            if action_name in seen:
                report.add_warning(
                    "duplicate_action", f"{path}.action", f"{phase}: {action_name} listed twice"
                )
            seen.add(action_name)

    def _expected_route(self, action_name: str, exposure_usd: float) -> str:
        if action_name == Action.BLOCK_CARD.value:
            return (
                Route.L1.value if exposure_usd <= self._block_card_l2_threshold else Route.L2.value
            )
        return FIXED_ROUTES[Action(action_name)].value

    def _exposure_clause(self, action_name: str, exposure_usd: float) -> str:
        if action_name != Action.BLOCK_CARD.value:
            return ""
        return f" at exposure ${exposure_usd:,.2f}"

    def _check_r10(
        self, case: Mapping[str, Any], actions: Mapping[str, Any], report: ValidationReport
    ) -> None:
        """R10: never BLOCK_ALL_CARDS without two confirmed cards or confirmed credential theft.

        v1 passed as soon as one connected card was listed, whatever the verdict, so
        an uncertain case with a single device link satisfied the validator and broke
        the policy. Read from the answer file alone, "confirmed" can only mean a
        ``fraud`` verdict: the alerted card plus at least one connected card makes
        two, and ``account_takeover`` is the one pattern that *is* a credential
        compromise.
        """
        verdict_is_fraud = case.get("verdict") == Verdict.FRAUD.value
        connected = case.get("connected_card_ids")
        two_cards = verdict_is_fraud and isinstance(connected, list) and len(connected) >= 1
        credentials = verdict_is_fraud and case.get("pattern") == Pattern.ACCOUNT_TAKEOVER.value
        if two_cards or credentials:
            return
        for phase in ("initial", "final"):
            items = actions.get(phase)
            if not isinstance(items, list):
                continue
            for index, item in enumerate(items):
                if isinstance(item, Mapping) and item.get("action") == Action.BLOCK_ALL_CARDS.value:
                    report.add_error(
                        "r10_block_all_cards",
                        f"next_best_actions.{phase}[{index}].action",
                        "R10 bars BLOCK_ALL_CARDS unless two of the customer's cards show confirmed "
                        "fraud (a 'fraud' verdict plus a connected card) or credentials are confirmed "
                        "compromised (pattern 'account_takeover')",
                    )


class SarConsistencyRule(ValidationRule):
    """The SAR block, and its agreement with the final actions.

    Responsibility: ``sar.file`` must agree with ``FILE_REPORT``; a filing needs a
    narrative of six to twelve sentences, subjects and exactly two activity dates;
    a non-filing must leave all four empty. ``sar.reason`` is REQUIRED in both
    branches — Guide.md says so and v1 never looked at it.
    Collaborators: ``ValidationReport``.
    """

    code = "sar"

    def check(self, answer: Mapping[str, Any], report: ValidationReport) -> None:
        sar = answer.get("sar")
        actions = answer.get("next_best_actions")
        if not isinstance(sar, Mapping):
            return

        if not nonempty_str(sar.get("reason")):
            report.add_error(
                "empty_field",
                "sar.reason",
                "sar.reason is required whether or not a report is filed: why file, or why not, citing the policy rule",
            )

        filing = sar.get("file")
        if not isinstance(filing, bool):
            report.add_error(
                "wrong_type", "sar.file", f"sar.file must be a boolean, got {filing!r}"
            )
            return

        if isinstance(actions, Mapping):
            final = actions.get("final")
            final_names = (
                {item.get("action") for item in final if isinstance(item, Mapping)}
                if isinstance(final, list)
                else set()
            )
            files_report = Action.FILE_REPORT.value in final_names
            if filing != files_report:
                report.add_error(
                    "sar_disagrees_with_actions",
                    "sar.file",
                    f"sar.file is {filing} but FILE_REPORT {'is' if files_report else 'is not'} "
                    "in the final actions",
                )

        if filing:
            self._check_filing(sar, report)
        else:
            self._check_not_filing(sar, report)

    def _check_filing(self, sar: Mapping[str, Any], report: ValidationReport) -> None:
        narrative = sar.get("narrative")
        if not nonempty_str(narrative):
            report.add_error(
                "empty_field", "sar.narrative", "sar.file is true but narrative is empty"
            )
        else:
            low, high = NARRATIVE_SENTENCES
            sentences = count_sentences(str(narrative))
            if not low <= sentences <= high:
                report.add_warning(
                    "narrative_length",
                    "sar.narrative",
                    f"narrative reads as {sentences} sentence(s); Guide.md asks for {low} to {high}",
                )
        if not sar.get("subjects"):
            report.add_error(
                "empty_field", "sar.subjects", "sar.file is true but subjects is empty"
            )
        dates = sar.get("activity_dates")
        if not isinstance(dates, list) or len(dates) != 2:
            report.add_error(
                "wrong_cardinality",
                "sar.activity_dates",
                "sar.activity_dates must hold exactly two dates, first and last",
            )
        else:
            malformed = [d for d in dates if not (isinstance(d, str) and _ISO_DATE.match(d))]
            if malformed:
                report.add_warning(
                    "date_format",
                    "sar.activity_dates",
                    f"activity dates {malformed} are not YYYY-MM-DD",
                )
            elif dates[0] > dates[1]:
                report.add_warning(
                    "date_order",
                    "sar.activity_dates",
                    f"activity_dates run backwards: {dates[0]} after {dates[1]}",
                )
        if not is_number(sar.get("total_amount_usd")) or float(sar.get("total_amount_usd", 0)) <= 0:
            report.add_warning(
                "sar_total",
                "sar.total_amount_usd",
                "sar.file is true but total_amount_usd is not a positive number",
            )

    def _check_not_filing(self, sar: Mapping[str, Any], report: ValidationReport) -> None:
        if sar.get("narrative"):
            report.add_error(
                "sar_not_empty", "sar.narrative", 'sar.file is false, so narrative must be ""'
            )
        if sar.get("subjects"):
            report.add_error(
                "sar_not_empty", "sar.subjects", "sar.file is false, so subjects must be []"
            )
        if sar.get("total_amount_usd"):
            report.add_error(
                "sar_not_empty",
                "sar.total_amount_usd",
                "sar.file is false, so total_amount_usd must be 0",
            )
        if sar.get("activity_dates"):
            report.add_error(
                "sar_not_empty",
                "sar.activity_dates",
                "sar.file is false, so activity_dates must be []",
            )


class LegitimateCaseRule(ValidationRule):
    """The case's internal consistency, of which the legitimate invariant is the strictest.

    Responsibility: a ``legitimate`` verdict forces an empty episode, zero exposure
    and no filing; ``first_suspicious_txn_id`` must sit inside the episode it starts;
    ``written_to_graph`` must carry the id of the vertex written.
    Collaborators: ``ValidationReport``.
    """

    code = "case_consistency"

    def check(self, answer: Mapping[str, Any], report: ValidationReport) -> None:
        case = answer.get("case")
        if not isinstance(case, Mapping):
            return
        sar = answer.get("sar")
        verdict, status = case.get("verdict"), case.get("status")

        if verdict == Verdict.LEGITIMATE.value:
            if case.get("affected_txn_ids"):
                report.add_error(
                    "legitimate_not_clean",
                    "case.affected_txn_ids",
                    "verdict is legitimate, so affected_txn_ids must be empty",
                )
            if case.get("exposure_usd"):
                report.add_error(
                    "legitimate_not_clean",
                    "case.exposure_usd",
                    "verdict is legitimate, so exposure_usd must be 0",
                )
            if isinstance(sar, Mapping) and sar.get("file"):
                report.add_error(
                    "legitimate_not_clean",
                    "sar.file",
                    "verdict is legitimate, so sar.file must be false",
                )

        contradiction = {
            Verdict.LEGITIMATE.value: CaseStatus.CLOSED_FRAUD.value,
            Verdict.FRAUD.value: CaseStatus.CLOSED_LEGITIMATE.value,
        }
        if verdict in contradiction and status == contradiction[str(verdict)]:
            report.add_warning(
                "status_contradicts_verdict",
                "case.status",
                f"status '{status}' contradicts verdict '{verdict}'",
            )

        first = case.get("first_suspicious_txn_id")
        affected = case.get("affected_txn_ids")
        if nonempty_str(first) and isinstance(affected, list) and first not in affected:
            report.add_error(
                "first_suspicious_not_in_episode",
                "case.first_suspicious_txn_id",
                f"first_suspicious_txn_id '{first}' is not listed in affected_txn_ids",
            )

        if case.get("written_to_graph") and not nonempty_str(case.get("graph_case_id")):
            report.add_error(
                "empty_field",
                "case.graph_case_id",
                "written_to_graph is true but graph_case_id is empty",
            )
        if not case.get("written_to_graph") and nonempty_str(case.get("graph_case_id")):
            report.add_warning(
                "unexpected_value",
                "case.graph_case_id",
                "graph_case_id is set but written_to_graph is false",
            )


class EvidenceRequestRule(ValidationRule):
    """The evidence requests, and what they imply about ``initial`` versus ``final``.

    Responsibility: each request's type, step number and assumed response; the rule
    that ``final`` equals ``initial`` when nothing was asked; and that every
    ``evidence_request:N`` reference in the evidence points at a request that exists.
    Collaborators: ``ValidationReport``. The reference is parsed here rather than
    through ``tools.refs.EvidenceRef`` so that validation stays runnable with no
    tooling and no connection.
    """

    code = "evidence_requests"

    def check(self, answer: Mapping[str, Any], report: ValidationReport) -> None:
        requests = answer.get("evidence_requests")
        if not isinstance(requests, list):
            return

        for index, request in enumerate(requests):
            path = f"evidence_requests[{index}]"
            if not isinstance(request, Mapping):
                continue
            if request.get("type") not in REQUEST_TYPE_VALUES:
                report.add_error(
                    "unknown_value",
                    f"{path}.type",
                    f"{path}.type {request.get('type')!r} not in {sorted(REQUEST_TYPE_VALUES)}",
                )
            step = request.get("asked_after_step")
            if not isinstance(step, int) or isinstance(step, bool) or step < 1:
                report.add_error(
                    "wrong_type",
                    f"{path}.asked_after_step",
                    f"asked_after_step must be the 1-based step number the request was made after, got {step!r}",
                )
            if not nonempty_str(request.get("assumed_response")):
                report.add_error(
                    "empty_field",
                    f"{path}.assumed_response",
                    "assumed_response is required: the response assumed, and the basis for assuming it",
                )

        self._check_final_matches_initial(answer, requests, report)
        self._check_references(answer, requests, report)

    def _check_final_matches_initial(
        self, answer: Mapping[str, Any], requests: list[Any], report: ValidationReport
    ) -> None:
        actions = answer.get("next_best_actions")
        if requests or not isinstance(actions, Mapping):
            return
        if actions.get("initial") != actions.get("final"):
            report.add_error(
                "final_differs_without_request",
                "next_best_actions.final",
                "no evidence was requested, so 'final' must equal 'initial', reasons included",
            )

    def _check_references(
        self, answer: Mapping[str, Any], requests: list[Any], report: ValidationReport
    ) -> None:
        case = answer.get("case")
        if not isinstance(case, Mapping) or not isinstance(case.get("evidence"), list):
            return
        for index, item in enumerate(case["evidence"]):
            if not isinstance(item, Mapping):
                continue
            match = _EVIDENCE_REQUEST_REF.match(str(item.get("ref", "")))
            if match is None:
                continue
            ordinal = int(match.group(1))
            if not 1 <= ordinal <= len(requests):
                report.add_error(
                    "dangling_reference",
                    f"case.evidence[{index}].ref",
                    f"ref '{item.get('ref')}' points at evidence request {ordinal}, "
                    f"but the answer lists {len(requests)}",
                )


def default_rules(block_card_l2_threshold: float = 2_500.0) -> tuple[ValidationRule, ...]:
    """The five rules in the order ``AnswerValidator`` runs them, shape first."""
    return (
        ShapeRule(),
        RoutingRule(block_card_l2_threshold=block_card_l2_threshold),
        SarConsistencyRule(),
        LegitimateCaseRule(),
        EvidenceRequestRule(),
    )
