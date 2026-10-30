"""The answer contract — the twenty JSON files the benchmark is scored on.

Responsibility: the exact shape of one submitted answer, and the ten invariants
that hold *inside* a single file. Guide.md is blunt about the stakes — "missing
fields score zero for that part" — so field order in this module is the field
order in the file, and ``extra="forbid"`` means a stray key is a loud failure
rather than a silently dropped one.

Two checks that look like they belong here do not. ``exposure_usd ==
sum(abs(amt))`` over the affected transactions, and the existence of every id
named in an answer, both need TigerGraph; they live in
``sentinel/validation/graph_check.py`` so that these models stay pure and can run
inside the orchestrator before a file is ever written.

Collaborators: ``AnswerAssembler`` builds an ``AnswerFile`` from the
investigation context; ``AnswerValidator`` re-checks the assembled file against
the rules the models cannot see; ``eval/validate.py`` is the organiser-facing
mirror of the same contract.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sentinel.domain.enums import (
    Action,
    CaseStatus,
    EvidenceSource,
    Pattern,
    RequestType,
    Route,
    Verdict,
)

#: Guide.md, Part 2: the narrative "is what a regulator reads", six to twelve sentences.
SAR_NARRATIVE_MIN_SENTENCES = 6
SAR_NARRATIVE_MAX_SENTENCES = 12

#: Guide.md, Part 2: first and last date of the activity.
SAR_ACTIVITY_DATE_COUNT = 2

#: Abbreviations a FinCEN-style narrative actually contains. A full stop after
#: one of these is not a sentence end, and miscounting one costs the whole
#: answer file: the SAR model rejects a narrative outside 6-12 sentences, and
#: "billed in U.S. dollars" reads as two extra sentences without this list.
_ABBREVIATIONS = (
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "st",
    "no",
    "vs",
    "approx",
    "est",
    "inc",
    "ltd",
    "co",
    "corp",
    "u.s",
    "u.k",
    "e.g",
    "i.e",
    "etc",
    "al",
)

#: A sentence ends at .!? followed by whitespace and a capital — or by the end
#: of the string. Two distinct miscounts are excluded:
#:
#: - a decimal point, because ``eval/validate.py`` splits on "." alone and reads
#:   "Total unauthorized amount: $268.43." as two sentences;
#: - an abbreviation, handled by :func:`count_sentences` rather than here,
#:   because a negative lookbehind must be fixed-width and these are not.
_SENTENCE_END = re.compile(r"[.!?]+(?=\s+[\"\u2018\u201c(\[]?[A-Z0-9]|\s*$)")

#: The word immediately before a candidate terminator.
_TRAILING_WORD = re.compile(r"([A-Za-z.]+)$")


def count_sentences(text: str) -> int:
    """Sentences in a narrative, as a reader would count them.

    A decimal point is punctuation, and so is the full stop in "U.S." or
    "Approx.". Both are excluded, because the only consumer of this number
    rejects a narrative outside a six-to-twelve band and a single abbreviation
    would otherwise fail an answer that is perfectly well formed.
    """
    count = 0
    trailing = False
    for match in _SENTENCE_END.finditer(text):
        head = text[: match.start()]
        word = _TRAILING_WORD.search(head)
        if word is not None and word.group(1).rstrip(".").lower() in _ABBREVIATIONS:
            continue
        count += 1
        trailing = not text[match.end() :].strip()
    # Prose that ends without a terminator is still a sentence.
    if text.strip() and not trailing:
        count += 1
    return count


class Evidence(BaseModel):
    """One claim, where it came from, what backs it, and the ids it rests on.

    Responsibility: the atomic unit of the graded evidence list. Exactly four
    fields — the assembler may not attach scores, confidences or timestamps here,
    because anything beyond these four is an extra key in a scored file.
    Collaborators: ``EvidenceRef`` produces ``ref``; ``GraphIdentityChecker``
    reads ``entity_ids`` and proves each one exists.
    """

    model_config = ConfigDict(extra="forbid")

    claim: str
    source: EvidenceSource
    ref: str
    entity_ids: list[str] = Field(default_factory=list)


class EvidenceRequest(BaseModel):
    """One round of evidence the agent asked for and then assumed the answer to.

    Responsibility: record that the agent stopped and asked, at which step, and
    what it assumed came back — Guide.md supplies no customer replies, so the
    stated assumption is the only thing that makes ``final`` defensible.
    Collaborators: ``EvidenceSimulator`` fills ``assumed_response`` from graph
    facts; ``StepCounter`` supplies ``asked_after_step``.
    """

    model_config = ConfigDict(extra="forbid")

    type: RequestType
    asked_after_step: int = Field(ge=0)
    assumed_response: str


class ActionRecommendation(BaseModel):
    """One next best action with its approval route and the rule that justifies it.

    Responsibility: carry the three graded fields and nothing else. Whether the
    route is *correct* for the action is ``RoutingRule``'s job in the validation
    package — it needs ``exposure_usd``, which is on the case, not here.
    Collaborators: produced by ``PolicyEngine``; re-checked by ``RoutingRule``.
    """

    model_config = ConfigDict(extra="forbid")

    action: Action
    route: Route
    reason: str


class NextBestActions(BaseModel):
    """What the agent recommended before the requested evidence, and after it.

    Responsibility: hold both phases plus the one-line account of the difference.
    Collaborators: ``PolicyEngine`` runs twice, once per ``CaseState``;
    ``AnswerFile.final_equals_initial_without_requests`` ties the two phases to
    whether anything was actually asked.
    """

    model_config = ConfigDict(extra="forbid")

    initial: list[ActionRecommendation] = Field(default_factory=list)
    final: list[ActionRecommendation] = Field(default_factory=list)
    #: Guide.md asks for the literal string "nothing" when the phases agree.
    what_changed: str = "nothing"


class SarReport(BaseModel):
    """The regulatory filing, or the reasoned decision not to file one.

    Responsibility: enforce that the report is all-or-nothing. A half-filled SAR
    is worse than none: it reads as a filing to the grader and to a regulator
    while missing the fields either of them would act on.
    Collaborators: ``SarPolicy`` decides ``file`` and writes ``reason``;
    ``NarrationAgent`` writes ``narrative``; ``EpisodeScoper`` supplies
    ``total_amount_usd`` and the two activity dates.
    """

    model_config = ConfigDict(extra="forbid")

    file: bool
    reason: str
    narrative: str = ""
    subjects: list[str] = Field(default_factory=list)
    total_amount_usd: float = 0.0
    activity_dates: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def sar_empty_when_not_filing(self) -> SarReport:
        """No filing means the four report fields are empty, empty, 0 and empty."""
        if self.file:
            return self
        populated = [
            name
            for name, value in (
                ("narrative", self.narrative),
                ("subjects", self.subjects),
                ("total_amount_usd", self.total_amount_usd),
                ("activity_dates", self.activity_dates),
            )
            if value
        ]
        if populated:
            raise ValueError(
                "sar.file is false, so narrative/subjects/total_amount_usd/activity_dates "
                f"must be empty, empty, 0 and empty; populated: {', '.join(populated)}"
            )
        return self

    @model_validator(mode="after")
    def sar_complete_when_filing(self) -> SarReport:
        """A filing carries a six-to-twelve sentence narrative, subjects and two dates."""
        if not self.file:
            return self
        if not self.narrative.strip():
            raise ValueError("sar.file is true but narrative is empty")
        sentences = count_sentences(self.narrative)
        if not SAR_NARRATIVE_MIN_SENTENCES <= sentences <= SAR_NARRATIVE_MAX_SENTENCES:
            raise ValueError(
                f"sar.narrative has {sentences} sentences; Guide.md requires "
                f"{SAR_NARRATIVE_MIN_SENTENCES} to {SAR_NARRATIVE_MAX_SENTENCES}"
            )
        if not self.subjects:
            raise ValueError("sar.file is true but subjects is empty")
        if len(self.activity_dates) != SAR_ACTIVITY_DATE_COUNT:
            raise ValueError(
                "sar.activity_dates must hold exactly the first and last date of the "
                f"activity; got {len(self.activity_dates)}"
            )
        return self


class Case(BaseModel):
    """The bank's internal record of the investigation — Part 1 of the answer.

    Responsibility: the fifteen graded case fields and the five invariants that
    need nothing outside the case itself.
    Collaborators: ``EpisodeScoper`` fills the episode fields and
    ``exposure_usd``; ``EvidenceLedger`` fills ``fraud_probability``;
    ``CaseMemoryStore`` fills ``written_to_graph`` and ``graph_case_id``.
    """

    model_config = ConfigDict(extra="forbid")

    status: CaseStatus
    verdict: Verdict
    fraud_probability: float
    pattern: Pattern
    pattern_description: str = ""
    affected_txn_ids: list[str] = Field(default_factory=list)
    first_suspicious_txn_id: str = ""
    connected_card_ids: list[str] = Field(default_factory=list)
    connected_device_profiles: list[str] = Field(default_factory=list)
    exposure_usd: float = 0.0
    evidence: list[Evidence]
    similar_prior_cases: list[str] = Field(default_factory=list)
    summary: str
    written_to_graph: bool = False
    graph_case_id: str = ""

    @field_validator("fraud_probability")
    @classmethod
    def probability_in_range(cls, value: float) -> float:
        """Calibration is scored, so a probability outside 0..1 is a hard failure."""
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"case.fraud_probability {value!r} is not a number in 0..1")
        return value

    @field_validator("evidence")
    @classmethod
    def evidence_non_empty(cls, value: list[Evidence]) -> list[Evidence]:
        """An answer with no evidence is an assertion; the evidence list is what is graded."""
        if not value:
            raise ValueError("case.evidence is empty")
        return value

    @model_validator(mode="after")
    def undocumented_has_description(self) -> Case:
        """``undocumented`` and a description imply each other, in both directions."""
        described = bool(self.pattern_description.strip())
        if self.pattern is Pattern.UNDOCUMENTED and not described:
            raise ValueError("pattern is 'undocumented' but pattern_description is empty")
        if self.pattern is not Pattern.UNDOCUMENTED and described:
            raise ValueError(
                f"pattern_description is set but pattern is '{self.pattern.value}', "
                "not 'undocumented'"
            )
        return self

    @model_validator(mode="after")
    def first_suspicious_in_affected(self) -> Case:
        """Where the episode started has to be a member of the episode."""
        first = self.first_suspicious_txn_id
        if first and first not in self.affected_txn_ids:
            raise ValueError(f"first_suspicious_txn_id '{first}' is not listed in affected_txn_ids")
        return self

    @model_validator(mode="after")
    def written_to_graph_has_id(self) -> Case:
        """Claiming a write-back without the vertex id makes the claim uncheckable."""
        if self.written_to_graph and not self.graph_case_id.strip():
            raise ValueError("written_to_graph is true but graph_case_id is empty")
        return self


class AnswerFile(BaseModel):
    """One ``cases/<case_id>.json`` file, whole.

    Responsibility: the nine top-level fields, and the three invariants that span
    more than one part of the answer — the SAR against the final actions, the
    legitimate verdict against the episode and the SAR, and the two action phases
    against whether any evidence was actually requested.
    Collaborators: ``AnswerAssembler`` constructs it; ``AnswerValidator`` and
    ``GraphIdentityChecker`` check what these models structurally cannot.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    case: Case
    evidence_requests: list[EvidenceRequest] = Field(default_factory=list)
    next_best_actions: NextBestActions
    sar: SarReport
    stop_reason: str
    tool_calls: int = Field(ge=0)
    tokens: int = Field(ge=0)
    latency_s: float = Field(ge=0.0)

    @model_validator(mode="after")
    def sar_agrees_with_actions(self) -> AnswerFile:
        """``sar.file`` and ``FILE_REPORT`` in the final actions are the same decision."""
        filing_recommended = Action.FILE_REPORT in {
            rec.action for rec in self.next_best_actions.final
        }
        if self.sar.file != filing_recommended:
            raise ValueError(
                f"sar.file is {self.sar.file} but FILE_REPORT "
                f"{'is' if filing_recommended else 'is not'} in next_best_actions.final"
            )
        return self

    @model_validator(mode="after")
    def legitimate_is_clean(self) -> AnswerFile:
        """A legitimate verdict has no episode, no exposure and no filing."""
        if self.case.verdict is not Verdict.LEGITIMATE:
            return self
        if self.case.affected_txn_ids:
            raise ValueError("verdict is legitimate, so affected_txn_ids must be empty")
        if self.case.exposure_usd != 0:
            raise ValueError(
                f"verdict is legitimate, so exposure_usd must be 0, not {self.case.exposure_usd}"
            )
        if self.sar.file:
            raise ValueError("verdict is legitimate, so sar.file must be false")
        return self

    @model_validator(mode="after")
    def final_equals_initial_without_requests(self) -> AnswerFile:
        """Nothing was asked, so nothing can have changed — reason strings included.

        The comparison is deep on purpose. An agent that re-words its rationale
        between the two phases without asking anyone anything has invented a
        change it cannot account for in ``what_changed``.
        """
        if self.evidence_requests:
            return self
        if self.next_best_actions.final != self.next_best_actions.initial:
            raise ValueError(
                "evidence_requests is empty, so next_best_actions.final must equal "
                "next_best_actions.initial exactly, including every reason string"
            )
        return self

    def to_json(self) -> str:
        """The file as it is written to ``cases/``: two-space indent, keys in field order."""
        return json.dumps(self.model_dump(mode="json"), indent=2, ensure_ascii=False)
