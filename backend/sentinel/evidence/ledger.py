"""The evidence ledger: a running, auditable fraud probability.

Every finding is posted as a likelihood ratio drawn from the fitted table, and
the probability is the sigmoid of the accumulated log-odds. One data structure
serves three deliverables at once:

  * ``fraud_probability`` in the answer file, calibrated rather than asserted
  * the ``evidence`` list, because every posting carries its claim and its ref
  * the probability trajectory the UI draws and ``what_changed`` describes

Three rules keep it honest.

**Correlated evidence is capped by group.** "New device", "device never used on
this card" and "proxy present" are three phrasings of one observation. Uncapped
they multiply and a single fact drives the probability past 0.9 on its own.

**The cap binds the group total, not the increment.** See :meth:`EvidenceLedger.post`.

**Absence is evidence.** A detector that runs and finds nothing posts its
``lr_absent``, which is usually just below 1. That is what lets the agent argue a
case is legitimate instead of merely failing to find anything.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from sentinel.domain.enums import EvidenceSource, TriggerType
from sentinel.evidence.table import EvidenceLikelihoodTable

if TYPE_CHECKING:  # pragma: no cover - import for typing only
    from sentinel.domain.answer import Evidence

#: Total log-odds any one evidence group may contribute, in either direction.
#: exp(1.2) is about 3.3x, so one group can move 0.50 to roughly 0.77 and no
#: further.
GROUP_CAP = 1.2

#: The group a caller-judged posting lands in unless it names another.
JUDGEMENT_GROUP = "response"

#: Judgements are capped wider than fitted features: 1.2 * 1.5 = 1.8. A customer
#: denying the charge outright should be able to move the number further than any
#: single fitted detector.
JUDGEMENT_CAP_MULTIPLIER = 1.5

#: How far a group total must have moved for that group to count as support.
SUPPORT_EPSILON = 0.05

#: The fitted group the model's risk-score bands live in.
RISK_SCORE_GROUP = "risk_score"

#: The band a model-score alert implies on its own. The 900 cleared alerts in
#: the closed-case history are 100% at or above 0.70 with a mean of 0.881, so
#: this is what "the model raised an alert" is worth — and what the trigger
#: prior has already paid for. See :meth:`EvidenceLedger._already_spent`.
ALERTING_BAND = "risk_70_85"


@dataclass(frozen=True, slots=True)
class Posting:
    """One finding, and what it did to the probability.

    Responsibility: an immutable record of a single move — the likelihood ratio
    applied, the probability either side of it, and the claim and ref that make
    it auditable. Collaborators: created only by :class:`EvidenceLedger`; read by
    the trace view, ``what_changed`` and :meth:`as_evidence`, which is the answer
    file's ``evidence[]`` entry.
    """

    feature: str
    present: bool
    lr: float
    log_lr: float
    p_before: float
    p_after: float
    claim: str
    ref: str
    source: EvidenceSource = EvidenceSource.GRAPH
    entity_ids: tuple[str, ...] = ()
    capped: bool = False
    group: str = ""

    def as_evidence(self) -> Evidence:
        """This posting in the answer file's four-field evidence shape."""
        # Imported here, not at module scope: the ledger is arithmetic over the
        # fitted table and does not otherwise depend on the answer contract.
        from sentinel.domain.answer import Evidence

        return Evidence(
            claim=self.claim,
            source=self.source,
            ref=self.ref,
            entity_ids=list(self.entity_ids),
        )


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    """The two numbers the policy engine reads, frozen at one moment."""

    p: float
    independent_support: int
    postings: int


class EvidenceLedger:
    """Accumulates log-odds from posted findings and remembers why.

    Responsibility: hold the fraud probability and its full derivation — the
    prior, every posting, every group total and every cap that bit.

    Collaborators: :class:`~sentinel.evidence.table.EvidenceLikelihoodTable`
    prices each posting; ``FeatureExtractor`` decides what to post; the policy
    engine reads :attr:`p` and :meth:`independent_support`; the assembler reads
    :meth:`evidence`, :attr:`trajectory` and :meth:`top_drivers`.
    """

    def __init__(
        self,
        table: EvidenceLikelihoodTable,
        trigger_type: TriggerType | str = TriggerType.RISK_SCORE,
        group_cap: float = GROUP_CAP,
    ) -> None:
        self._table = table
        self._trigger_type = (
            trigger_type.value if isinstance(trigger_type, Enum) else str(trigger_type)
        )
        self._prior = table.prior_for(trigger_type)
        self._log_odds = math.log(self._prior / (1.0 - self._prior))
        self._postings: list[Posting] = []
        self._group_total: dict[str, float] = {}
        #: The widest cap any posting into this group has asked for. See _apply.
        self._group_cap_for: dict[str, float] = {}
        self._group_cap = group_cap

    # ── probability ──────────────────────────────────────────────────────────

    @property
    def p(self) -> float:
        """The current fraud probability."""
        return 1.0 / (1.0 + math.exp(-self._log_odds))

    @property
    def prior(self) -> float:
        """The prior the trigger type bought, before any posting."""
        return self._prior

    @property
    def trigger_type(self) -> str:
        """The trigger type this ledger was primed with."""
        return self._trigger_type

    @property
    def trajectory(self) -> list[float]:
        """The prior followed by the probability after each posting."""
        return [round(self._prior, 4)] + [posting.p_after for posting in self._postings]

    @property
    def postings(self) -> tuple[Posting, ...]:
        """Every posting, in the order it was made."""
        return tuple(self._postings)

    @property
    def group_totals(self) -> Mapping[str, float]:
        """Accumulated log-odds per group, after capping. A copy, not the map."""
        return dict(self._group_total)

    # ── posting ──────────────────────────────────────────────────────────────

    def post(
        self,
        feature: str,
        present: bool,
        claim: str,
        ref: str,
        entity_ids: Sequence[str] | None = None,
        source: EvidenceSource = EvidenceSource.GRAPH,
        weight: float = 1.0,
    ) -> Posting:
        """Post one finding as a likelihood ratio and move the probability.

        ``present`` False posts the feature's absence likelihood. Raises
        :class:`UnknownFeature` if the name is not in the fitted table — a typo
        must never silently post nothing.

        The cap binds the group *total*, not the increment. v1 clamped the
        increment by the room left in the group, so with a group at +0.5 an
        incoming -2.0 landed at -0.7 and left the group at -0.2 instead of -1.2:
        exonerating evidence arriving after incriminating evidence in the same
        group was suppressed, which damaged exactly the legitimate half of the
        benchmark.
        """
        lr = self._table.lookup(feature, present)
        return self._apply(
            feature=feature,
            present=present,
            lr=lr,
            log_lr=(math.log(lr) - self._already_spent(feature, present)) * weight,
            group=self._table.group_of(feature),
            cap=self._group_cap,
            claim=claim,
            ref=ref,
            source=source,
            entity_ids=entity_ids,
        )

    def post_judgement(
        self,
        group: str,
        log_lr: float,
        claim: str,
        ref: str,
        source: EvidenceSource = EvidenceSource.CUSTOMER,
        entity_ids: Sequence[str] | None = None,
        cap_multiplier: float = JUDGEMENT_CAP_MULTIPLIER,
    ) -> Posting:
        """Post a finding whose weight is a stated judgement, not a fitted one.

        Simulated customer responses and document-grounded findings have no
        entry in the table — the closed cases record outcomes, not what customers
        said mid-investigation — so the caller supplies ``log_lr`` directly. It is
        capped like anything else, just wider. This is the only way a posting's
        source becomes ``customer`` or ``document``.
        """
        return self._apply(
            feature=group,
            present=True,
            lr=math.exp(log_lr),
            log_lr=log_lr,
            group=group,
            cap=self._group_cap * cap_multiplier,
            claim=claim,
            ref=ref,
            source=source,
            entity_ids=entity_ids,
        )

    def _already_spent(self, feature: str, present: bool) -> float:
        """Log-odds this feature already contributed through the prior.

        One case, and it is the largest single calibration error the benchmark
        exposed. A ``risk_score`` alert exists *because* the model scored the
        transaction high. The trigger prior of 0.25 is P(fraud | the model
        raised this alert) — it has already spent the fact that the score is
        high. Posting the score band's own likelihood ratio on top counts the
        same evidence twice, and the ratios are large: 8.22 for the 0.70-0.85
        band, 18.76 above it.

        Measured: it carried eight of the eleven score-triggered benchmark
        cases past 0.85 to a ``fraud`` verdict on the strength of the score
        that raised them, in flat contradiction of Guide.md's own statement
        that "above 0.7, most flagged transactions turn out to be legitimate".

        The band is not discarded, because *how far above the threshold* a
        score sits is real information. It is re-centred on the band the alert
        itself implies. The 900 cleared alerts in the closed-case history are
        100% at or above 0.70 with a mean of 0.881, so the alerting band is
        0.70-0.85: a score there now contributes nothing beyond the prior, a
        score above it still contributes (+0.83), and a score below it argues
        the model was reaching (-0.41 and -1.20).

        No other trigger is affected. On a customer report or an analyst
        request the alert did not come from the model, so the model's score is
        independent information and its full ratio applies. Nor is an *absent*
        band affected: the prior spent a statement about where the score is,
        not about where it is not — and the extractor posts exactly one band,
        present, because the other four are the same fact restated.
        """
        if not present or self._trigger_type != TriggerType.RISK_SCORE.value:
            return 0.0
        if self._table.fitted_group_of(feature) != RISK_SCORE_GROUP:
            return 0.0
        return math.log(self._table.lookup(ALERTING_BAND, present=True))

    def _apply(
        self,
        *,
        feature: str,
        present: bool,
        lr: float,
        log_lr: float,
        group: str,
        cap: float,
        claim: str,
        ref: str,
        source: EvidenceSource,
        entity_ids: Sequence[str] | None,
    ) -> Posting:
        used = self._group_total.get(group, 0.0)
        # The cap is a property of the GROUP, not of the call that happens to
        # be posting into it. `cap` arrives per call — 1.2 from a fitted
        # feature, 1.8 from a judgement — and if a narrower one arrived after a
        # total had already grown past it, `clamped - used` was negative
        # whatever the sign of the increment: +0.4 applied as -0.5, recorded
        # that way in the trace the console renders. Widening keeps the group's
        # own ceiling monotonic, so an increment can never change sign.
        cap = max(cap, self._group_cap_for.get(group, 0.0))
        self._group_cap_for[group] = cap
        # The cap binds the resulting total, not the increment: see post().
        proposed = used + log_lr
        clamped = max(-cap, min(cap, proposed))
        capped = clamped != proposed
        log_lr = clamped - used
        self._group_total[group] = clamped

        p_before = self.p
        self._log_odds += log_lr
        posting = Posting(
            feature=feature,
            present=present,
            lr=round(lr, 4),
            log_lr=round(log_lr, 4),
            p_before=round(p_before, 4),
            p_after=round(self.p, 4),
            claim=claim,
            ref=ref,
            source=source,
            entity_ids=tuple(entity_ids or ()),
            capped=capped,
            group=group,
        )
        self._postings.append(posting)
        return posting

    # ── outputs ──────────────────────────────────────────────────────────────

    def evidence(self) -> list[Evidence]:
        """Every posting in the answer file's evidence shape, in posting order."""
        return [posting.as_evidence() for posting in self._postings]

    def independent_support(self) -> int:
        """How many distinct evidence groups actually moved the number.

        Policy section 6 requires two independent pieces of evidence before
        stopping, so "independent" has to mean distinct groups, not distinct
        postings — otherwise three restatements of one device finding would
        satisfy it.
        """
        return sum(1 for total in self._group_total.values() if abs(total) > SUPPORT_EPSILON)

    def snapshot(self) -> LedgerSnapshot:
        """What the policy reads off this ledger, frozen at this instant.

        ``next_best_actions.initial`` means "what I recommended before I asked
        for anything", and the ledger is a running total — so by the time
        ``decide`` runs, the requested evidence has already moved it. Reading
        ``p`` there gave the *post*-request probability to the *pre*-request
        recommendation: HHG-006's requested evidence moved the number from 0.81
        to 0.59 and the initial recommendation was computed at 0.59, which is
        the one number it cannot be. The snapshot taken before the request is
        what `initial` is evaluated against.
        """
        return LedgerSnapshot(
            p=self.p,
            independent_support=self.independent_support(),
            postings=len(self._postings),
        )

    def top_drivers(self, n: int = 3) -> list[Posting]:
        """The postings that moved the number most, for ``what_changed``."""
        return sorted(self._postings, key=lambda p: abs(p.log_lr), reverse=True)[:n]

    def explain(self) -> str:
        """The whole derivation as text, for logs and the trace panel."""
        lines = [f"prior {self._prior:.2f} ({self._trigger_type})"]
        for posting in self._postings:
            arrow = "up  " if posting.log_lr > 0 else "down"
            mark = " [capped]" if posting.capped else ""
            lines.append(
                f"  {posting.p_before:.2f} -> {posting.p_after:.2f}  {arrow} "
                f"LR {posting.lr:>6.2f}  {posting.feature}{mark}"
            )
        lines.append(f"final {self.p:.2f}")
        return "\n".join(lines)
