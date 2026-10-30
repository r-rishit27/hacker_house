"""Turning a finished investigation into the file that gets graded.

This is where the rule that no answer field comes from free-form model text
stops being an intention and becomes a type. Every value below is either read
off a computed object — the policy engine's recommendations, the ledger's
probability, the episode scoper's exposure, the tool log's counters — or taken
from ``Narration``, whose fields are the seven prose strings the brief allows.

There is no third source.
"""

from __future__ import annotations

from dataclasses import dataclass

from sentinel.agents.context import InvestigationContext
from sentinel.domain.answer import (
    ActionRecommendation,
    AnswerFile,
    Case,
    Evidence,
    EvidenceRequest,
    NextBestActions,
    SarReport,
)
from sentinel.domain.enums import Action, CaseStatus, EvidenceSource, Pattern, Verdict
from sentinel.policy.engine import cited_rules
from sentinel.tools.refs import EvidenceRef


@dataclass
class AnswerAssembler:
    """Builds the answer file from the context, and nothing else.

    Responsibility: field-by-field construction of the graded artefact, plus the
    two consistency choices the format forces — status follows verdict and the
    actions taken, and `final` deep-equals `initial` when nothing was asked.
    Collaborators: read-only over ``InvestigationContext``.
    """

    created_by: str = "sentinel"

    def assemble(self, ctx: InvestigationContext) -> AnswerFile:
        episode = ctx.episode
        affected = list(episode.affected_txn_ids) if episode else []
        exposure = episode.exposure_usd if episode else 0.0
        first = episode.first_suspicious_txn_id if episode else ""

        # A legitimate verdict forces an empty episode, zero exposure, no
        # report — and no pattern. The scoper already enforces the first two;
        # this is the second gate, because the contract is checked on the file,
        # not on the object. `pattern` is "the fraud pattern you identified",
        # so naming one on a case just concluded legitimate contradicts the
        # verdict beside it. The pattern still steers the investigation; it is
        # only the *reported* value that follows the conclusion.
        pattern = ctx.pattern
        description = ctx.narration.pattern_description
        if ctx.verdict is Verdict.LEGITIMATE:
            affected, exposure, first = [], 0.0, ""
            pattern, description = Pattern.NONE, ""

        case = Case(
            status=self._status(ctx),
            verdict=ctx.verdict,
            fraud_probability=round(ctx.ledger.p, 4),
            pattern=pattern,
            pattern_description=description,
            affected_txn_ids=affected,
            first_suspicious_txn_id=first,
            connected_card_ids=list(ctx.connected_card_ids),
            connected_device_profiles=list(ctx.connected_device_profiles),
            exposure_usd=exposure,
            evidence=self._evidence(ctx),
            similar_prior_cases=list(ctx.similar_prior_cases),
            summary=ctx.narration.summary,
            # Claimed only when `WriteStep` actually wrote the vertex. The
            # assembler never sets these to True on its own.
            written_to_graph=ctx.written_to_graph,
            graph_case_id=ctx.graph_case_id,
        )

        return AnswerFile(
            case_id=ctx.alert.alert_id,
            case=case,
            evidence_requests=self._requests(ctx),
            next_best_actions=self._actions(ctx),
            sar=self._sar(ctx, exposure, affected),
            stop_reason=ctx.narration.stop_reason,
            tool_calls=ctx.tools.log.count,
            tokens=ctx.budget.tokens,
            latency_s=round(ctx.budget.elapsed_s, 2),
        )

    # ── the parts ────────────────────────────────────────────────────────────

    @staticmethod
    def _status(ctx: InvestigationContext) -> CaseStatus:
        """Where the case stands, read off the verdict and what was recommended.

        `open` means evidence is still pending, which cannot be true here: the
        run has finished and the simulated response has already come back.
        """
        actions = {rec.action for rec in ctx.final}
        if Action.ESCALATE_TO_ANALYST in actions:
            return CaseStatus.ESCALATED
        if ctx.verdict is Verdict.FRAUD:
            return CaseStatus.CLOSED_FRAUD
        if ctx.verdict is Verdict.LEGITIMATE:
            return CaseStatus.CLOSED_LEGITIMATE
        return CaseStatus.OPEN

    @staticmethod
    def _evidence(ctx: InvestigationContext) -> list[Evidence]:
        """Every ledger posting, then the policy text the decision rests on.

        The two halves answer different questions and the answer format wants
        both. A posting says what the graph showed and how much it moved the
        probability; a document item says which written rule turned that into
        this action. Guide.md §7 asks for the rule number; this is where the
        rule stops being a string in a reason and becomes a citation a reader
        can follow back to `PolicyDoc`.
        """
        items = [
            Evidence(
                claim=posting.claim,
                source=posting.source,
                ref=posting.ref,
                entity_ids=list(posting.entity_ids),
            )
            for posting in ctx.ledger.postings
        ]
        return items + AnswerAssembler._documents(ctx)

    @staticmethod
    def _documents(ctx: InvestigationContext) -> list[Evidence]:
        """One item per policy rule the engine actually decided under.

        The claim is computed — the rule's own title from the retrieved chunk,
        plus the engine's own reason. No model text reaches it.
        """
        # The engine's own reason, stripped of the rule prefix it opens with —
        # "R7: send an informational message" reads as "Fraud Policy R7 ...
        # applied here because R7: ..." otherwise.
        reasons: dict[str, str] = {}
        for rec in (*ctx.initial, *ctx.final):
            for rule in cited_rules([rec]):
                reasons.setdefault(rule, rec.reason.split(":", 1)[-1].strip())

        out: list[Evidence] = []
        for doc_id in ctx.applied_rules:
            entry = ctx.policy_catalogue.get(doc_id, {})
            rule = doc_id.removeprefix("POL-")
            # The section chunks already title themselves "Fraud Policy §3a";
            # the rule chunks title themselves "R7. ...". One prefix, not two.
            title = str(entry.get("title") or f"§{rule}").removeprefix("Fraud Policy ")
            because = reasons.get(rule) or (ctx.sar_reason if doc_id == "POL-3A" else "")
            claim = f"Fraud Policy {title}" + (
                f" — applied here because {because}" if because else ""
            )
            out.append(
                Evidence(
                    claim=claim,
                    source=EvidenceSource.DOCUMENT,
                    ref=str(entry.get("ref") or EvidenceRef.document(doc_id, rule)),
                    entity_ids=[doc_id],
                )
            )
        return out

    @staticmethod
    def _requests(ctx: InvestigationContext) -> list[EvidenceRequest]:
        if ctx.response is None or ctx.evidence_request_step is None:
            return []
        return [
            EvidenceRequest(
                type=ctx.response.request_type,
                # The same counter the SSE stream numbered its steps with.
                asked_after_step=ctx.evidence_request_step,
                assumed_response=ctx.response.assumed_response,
            )
        ]

    @staticmethod
    def _actions(ctx: InvestigationContext) -> NextBestActions:
        initial = [
            ActionRecommendation(action=r.action, route=r.route, reason=r.reason)
            for r in ctx.initial
        ]
        if ctx.response is None:
            # Nothing was asked, so the two lists must be identical — the
            # validator compares them deeply, reason strings included.
            return NextBestActions(initial=initial, final=list(initial), what_changed="nothing")
        final = [
            ActionRecommendation(action=r.action, route=r.route, reason=r.reason) for r in ctx.final
        ]
        return NextBestActions(
            initial=initial,
            final=final,
            what_changed=ctx.narration.what_changed or "nothing",
        )

    @staticmethod
    def _sar(ctx: InvestigationContext, exposure: float, affected: list[str]) -> SarReport:
        """The filing decision comes from the policy engine, never from prose."""
        if not ctx.sar_file:
            return SarReport(
                file=False,
                reason=ctx.sar_reason,
                narrative="",
                subjects=[],
                total_amount_usd=0,
                activity_dates=[],
            )
        subjects = [ctx.alert.customer_id, ctx.alert.card_id, *ctx.connected_card_ids]
        return SarReport(
            file=True,
            reason=ctx.sar_reason,
            narrative=ctx.narration.sar_narrative,
            subjects=[s for s in dict.fromkeys(subjects) if s],
            total_amount_usd=exposure,
            activity_dates=_activity_dates(ctx, affected),
        )


def _activity_dates(ctx: InvestigationContext, affected: list[str]) -> list[str]:
    """First and last date of the episode, `YYYY-MM-DD`, exactly two entries."""
    rows = []
    if ctx.window is not None:
        rows = [row for row in ctx.window.window if row.txn_id in set(affected)]
    if not rows and ctx.txn is not None:
        rows = [ctx.txn]
    stamps = sorted(row.ts for row in rows if row.ts is not None)
    if not stamps:
        return []
    return [stamps[0].strftime("%Y-%m-%d"), stamps[-1].strftime("%Y-%m-%d")]
