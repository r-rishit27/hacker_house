"""The prose of the case file, written after every fact is settled.

By the time this runs, the verdict, the probability, the actions, the routes,
the exposure and the ids are all computed. The narrator writes sentences about
them and may not change one. That is why its output model is three strings.

The SAR narrative is the one place the brief asks for completeness rather than
brevity, and it is judged against FinCEN's narrative standard: who, what, when,
where, how, why, in six to twelve sentences, standing on its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from sentinel.agents.agents.base import LlmAgent, bullet
from sentinel.agents.context import InvestigationContext
from sentinel.llm.prompts import NARRATION_SYSTEM


class NarrationOutput(BaseModel):
    sar_narrative: str = ""
    what_changed: str = ""
    stop_reason: str = ""


@dataclass
class NarrationAgent(LlmAgent[NarrationOutput]):
    """Writes the SAR narrative, `what_changed` and `stop_reason`."""

    purpose: str = "sar_narrative"
    schema: type[BaseModel] = NarrationOutput
    system: str = NARRATION_SYSTEM
    max_output_tokens: int = 1_400

    def render(self, ctx: InvestigationContext) -> str:
        episode = ctx.episode
        initial = bullet([f"{r.action.value} ({r.route.value}) — {r.reason}" for r in ctx.initial])
        final = bullet([f"{r.action.value} ({r.route.value}) — {r.reason}" for r in ctx.final])
        evidence = bullet([p.claim for p in ctx.ledger.postings])
        txn = ctx.txn

        lines = [
            f"Case {ctx.alert.alert_id}. Customer {ctx.alert.customer_id}, "
            f"card {ctx.alert.card_id}.",
            f"Verdict {ctx.verdict.value} at probability {ctx.ledger.p:.2f}. "
            f"Pattern: {ctx.pattern.value}.",
        ]
        if txn is not None:
            lines.append(
                f"Flagged transaction {txn.txn_id}: ${txn.amt:,.2f} on {txn.ts}, "
                f"{txn.channel}, billing region {txn.addr1 or 'not recorded'}."
            )
        if episode is not None:
            lines.append(
                f"Episode: {len(episode.affected_txn_ids)} transaction(s), exposure "
                f"${episode.exposure_usd:,.2f}. Transaction ids: "
                f"{', '.join(episode.affected_txn_ids) or 'none'}."
            )
        if ctx.connected_card_ids:
            lines.append(f"Connected cards: {', '.join(ctx.connected_card_ids)}.")
        if ctx.connected_device_profiles:
            lines.append(f"Device profiles: {'; '.join(ctx.connected_device_profiles)}.")
        if ctx.similar_prior_cases:
            lines.append(f"Prior cases drawn on: {', '.join(ctx.similar_prior_cases)}.")
        if ctx.response is not None:
            lines.append(
                f"An evidence request was made and the assumed response was: "
                f"{ctx.response.assumed_response}"
            )
        lines.append(f"A suspicious activity report {'IS' if ctx.sar_file else 'is NOT'} required.")
        lines.append(f"The policy's reason: {ctx.sar_reason}")

        return (
            f"{bullet(lines)}\n\n"
            f"Evidence recorded:\n{evidence}\n\n"
            f"Initial actions:\n{initial or '- none'}\n\n"
            f"Final actions:\n{final or '- none'}\n\n"
            + (
                "Write the SAR narrative in full.\n"
                if ctx.sar_file
                else "No report is being filed, so `sar_narrative` must be an empty string.\n"
            )
            + "Then write what_changed and stop_reason."
        )

    def fallback(self, ctx: InvestigationContext) -> NarrationOutput:
        """Templated prose, so a model outage costs quality rather than a file.

        A missing narrative on a case that files a report is a validation error
        and the file is quarantined, which is the correct outcome: an empty SAR
        narrative would score zero anyway and a fabricated one is worse.
        """
        changed = (
            "nothing"
            if ctx.response is None
            else (
                f"The cardholder's response ({ctx.response.branch.value}) moved the probability "
                f"to {ctx.ledger.p:.2f}, and the recommendation was recomputed under policy."
            )
        )
        return NarrationOutput(
            sar_narrative="",
            what_changed=changed,
            stop_reason=(
                f"The investigation ended at probability {ctx.ledger.p:.2f} with "
                f"{ctx.ledger.independent_support()} independent evidence group(s)."
            ),
        )
