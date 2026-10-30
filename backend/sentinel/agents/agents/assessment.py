"""Naming the pattern, writing the claims, and arguing the other side.

Two agents, one step. The assessor says what the evidence shows; the defence
then makes the strongest honest case that the activity is legitimate. The second
exists because the failure mode this benchmark punishes hardest is blocking real
customers, and a single pass that has already decided "fraud" will not go
looking for the facts that say otherwise.

The defence is bounded. It may only cite features that are in the fitted table
and citations that already exist in the trace, and everything it posts runs
through the same group cap as every other posting — so it can move a probability
but cannot manufacture one.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from sentinel.agents.agents.base import LlmAgent, bullet
from sentinel.agents.context import InvestigationContext
from sentinel.domain.enums import EvidenceSource, Pattern
from sentinel.evidence.extractor import PostingRequest
from sentinel.llm.prompts import ASSESSMENT_SYSTEM, EXONERATION_SYSTEM


class AssessmentOutput(BaseModel):
    """What the assessor must return. `pattern` is an enum, so it cannot invent one."""

    pattern: Pattern
    pattern_description: str = ""
    summary: str = ""


class ExonerationItem(BaseModel):
    feature: str
    claim: str
    ref: str


class ExonerationOutput(BaseModel):
    exonerating: list[ExonerationItem] = Field(default_factory=list)
    rebuttal: str = ""


@dataclass
class AssessmentAgent(LlmAgent[AssessmentOutput]):
    """Names the pattern and writes the case summary from computed facts."""

    purpose: str = "claims"
    schema: type[BaseModel] = AssessmentOutput
    system: str = ASSESSMENT_SYSTEM
    max_output_tokens: int = 900

    def render(self, ctx: InvestigationContext) -> str:
        txn = ctx.txn
        facts: list[str] = []
        if txn is not None:
            facts += [
                f"Flagged transaction {txn.txn_id}: ${txn.amt:,.2f}, {txn.channel}, "
                f"product code {txn.product}, billing region {txn.addr1 or 'not recorded'}, "
                f"at {txn.ts}.",
                f"The bank's model scored it {txn.risk_score:.2f}. The score is an input, "
                "never a verdict.",
            ]
        facts.append(f"The alert was raised by: {ctx.alert.trigger_type.value}.")
        facts.append(f"Trigger text: {ctx.alert.trigger_text}")

        # Everything the ledger has been told, in the words it recorded.
        for posting in ctx.ledger.postings:
            direction = "toward fraud" if posting.lr > 1 else "toward legitimate"
            facts.append(f"{posting.claim} ({direction}, LR {posting.lr:.2f})")

        if ctx.similar_prior_cases:
            facts.append(
                "Prior closed cases retrieved for this card or customer: "
                + ", ".join(ctx.similar_prior_cases)
            )
        for hit in ctx.retrieved[:4]:
            facts.append(f"{hit.get('title', '')}: {hit.get('snippet', '')}")

        return (
            f"Case {ctx.alert.alert_id}, card {ctx.alert.card_id}, "
            f"customer {ctx.alert.customer_id}.\n"
            f"Accumulated fraud probability: {ctx.ledger.p:.2f} "
            f"(it started at {ctx.ledger.prior:.2f} on the trigger type alone).\n\n"
            f"Facts:\n{bullet(facts)}\n\n"
            "Name the pattern and summarise. If the facts point to legitimate "
            "activity, say so plainly."
        )

    def fallback(self, ctx: InvestigationContext) -> AssessmentOutput:
        """No model: no pattern claimed, and a summary built from the numbers.

        Claiming a pattern without a model would be the code guessing, which is
        the thing this whole architecture exists to avoid.
        """
        drivers = ", ".join(p.claim for p in ctx.ledger.top_drivers(2)) or "no strong evidence"
        return AssessmentOutput(
            pattern=Pattern.NONE,
            pattern_description="",
            summary=(
                f"Alert {ctx.alert.alert_id} on card {ctx.alert.card_id} was assessed from "
                f"{len(ctx.ledger.postings)} graph findings, ending at a fraud probability of "
                f"{ctx.ledger.p:.2f} from a prior of {ctx.ledger.prior:.2f}. "
                f"The strongest evidence was: {drivers}. "
                "No pattern is claimed because the narrative model was unavailable for this run."
            ),
        )


@dataclass
class DevilsAdvocateAgent(LlmAgent[ExonerationOutput]):
    """Argues the legitimate reading, within the bounds of what was measured."""

    purpose: str = "exonerate"
    schema: type[BaseModel] = ExonerationOutput
    system: str = EXONERATION_SYSTEM
    #: The defence lists several findings with their citations, so it needs more
    #: room than the other agents. Measured: 700 truncated its reply mid-JSON.
    max_output_tokens: int = 1_800

    def render(self, ctx: InvestigationContext) -> str:
        postings = bullet(
            [
                f"{p.feature}: {p.claim} (LR {p.lr:.2f}, cited as {p.ref})"
                for p in ctx.ledger.postings
            ]
        )
        fitted = ", ".join(sorted(ctx.extractor.table.feature_names))
        return (
            f"Case {ctx.alert.alert_id}. The assessment so far puts the fraud probability at "
            f"{ctx.ledger.p:.2f}, and the pattern named is {ctx.pattern.value}.\n\n"
            f"Every finding already recorded:\n{postings}\n\n"
            f"Features you may name: {fitted}\n\n"
            "Make the strongest honest case that this activity is legitimate. Cite only "
            "findings above."
        )

    def fallback(self, ctx: InvestigationContext) -> ExonerationOutput:
        return ExonerationOutput(
            exonerating=[],
            rebuttal="No defence argument was made: the model was unavailable for this run.",
        )

    @staticmethod
    def to_postings(output: ExonerationOutput, ctx: InvestigationContext) -> list[PostingRequest]:
        """Keep only what the fitted table knows and the trace already cites.

        The defence is the one agent whose output becomes evidence, so it is the
        one place a model could otherwise move the probability with an invented
        fact. Two gates: the feature must be fitted, and the citation must be a
        query this investigation actually ran.
        """
        known_refs = set(ctx.tools.log.refs())
        already = {posting.feature for posting in ctx.ledger.postings}
        out: list[PostingRequest] = []
        for item in output.exonerating:
            if not ctx.extractor.table.has(item.feature):
                continue
            if item.feature in already:
                continue  # the ledger already has this one; twice is double-counting
            if item.ref not in known_refs:
                continue
            out.append(
                PostingRequest(
                    feature=item.feature,
                    present=False,
                    claim=item.claim,
                    ref=item.ref,
                    entity_ids=(ctx.alert.card_id,),
                    source=EvidenceSource.GRAPH,
                )
            )
        return out
