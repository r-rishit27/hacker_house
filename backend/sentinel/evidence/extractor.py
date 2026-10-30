"""Turn normalised tool results into ledger postings.

This is the only place in the system that decides *which* fitted feature a graph
fact is. Getting it wrong is invisible: the probability simply comes out wrong,
and nothing downstream can tell.

Three rules shape everything here.

**Exclusive families post once.** The five risk bands partition the score, so
exactly one is posted as present and the other four are not posted at all. Their
``lr_absent`` values are not independent evidence — they are the same observation
restated, and posting all five would let one fact move the probability five times.
The same applies to ``m_flags_any_false`` / ``m_flags_all_true``.

**Unobservable is not absent.** Seven of the twenty benchmark alerts are
card-present and carry no identity record at all. ``device_new`` is then neither
present nor absent — it is unknown, and posting ``lr_absent`` would claim
evidence the graph never supplied. Every device feature is gated on the identity
record existing.

**Absence is evidence where it is genuinely observed.** A card that *does* have
an identity record and is *not* flagged new gets ``device_new`` posted absent, at
LR 0.959. That is how the system argues for legitimacy rather than merely failing
to find fraud, and it is what protects the roughly half of the benchmark that is
legitimate.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sentinel.domain.enums import EvidenceSource
from sentinel.evidence.table import EvidenceLikelihoodTable
from sentinel.tools.dto import (
    AmountBandProbe,
    CardAmountStats,
    DeviceNovelty,
    ProductNovelty,
    RegionNovelty,
    RingExpansion,
    TxnDetail,
    TxnSequenceContext,
)

#: Below this many prior transactions a card has no stable baseline, so the
#: amount, region and product comparisons are not made. The number is the one
#: the offline fit used in its predicates.
MIN_PRIOR_FOR_BASELINE = 10

#: The home country code. `addr2` is the billing country and 87 is home.
HOME_COUNTRY = "87.0"

#: `device_profile_generic` fires above this many cards on one fingerprint.
GENERIC_DEVICE_CARDS = 20


@dataclass(frozen=True, slots=True)
class PostingRequest:
    """One feature, ready to post, with the sentence and citation it carries."""

    feature: str
    present: bool
    claim: str
    ref: str
    entity_ids: tuple[str, ...] = ()
    source: EvidenceSource = EvidenceSource.GRAPH

    def as_kwargs(self) -> dict[str, object]:
        return {
            "feature": self.feature,
            "present": self.present,
            "claim": self.claim,
            "ref": self.ref,
            "entity_ids": list(self.entity_ids),
            "source": self.source,
        }


@dataclass
class FeatureExtractor:
    """Reads tool DTOs, decides which fitted features they evidence.

    Responsibility: the mapping from graph facts to the 29 features in the fitted
    table, and the honest English sentence each one carries into ``evidence[]``.
    It does not post — the caller does — so extraction is testable without a
    ledger and the same request can be inspected before it moves anything.

    Collaborators: the tool DTOs in ``sentinel.tools.dto`` supply facts;
    :class:`~sentinel.evidence.table.EvidenceLikelihoodTable` is consulted only to
    refuse a feature name that is not fitted, which turns a typo into an error
    instead of a silent no-op.
    """

    table: EvidenceLikelihoodTable
    requests: list[PostingRequest] = field(default_factory=list)

    # ── the flagged transaction ──────────────────────────────────────────────

    def from_txn_detail(self, detail: TxnDetail, ref: str) -> list[PostingRequest]:
        """Risk band, channel, amount bands, country, match flags and distance."""
        txn = detail.txn
        ids = (txn.txn_id,)
        out: list[PostingRequest] = []

        # The model score is the TRANSACTION's, never the alert's: Alert.risk_score
        # is -1 on nine of the twenty, which is the missing-numeric sentinel and
        # would land those cases in the bottom band at LR 0.372.
        band, label = self._risk_band(txn.risk_score)
        out.append(
            self._req(
                band,
                True,
                f"The bank's model scored this transaction {txn.risk_score:.2f}, {label}.",
                ref,
                ids,
            )
        )

        out.append(
            self._req(
                "channel_online",
                txn.channel == "online",
                (
                    "The purchase was made online, without the card present."
                    if txn.channel == "online"
                    else "The purchase was card-present, so the card itself was used."
                ),
                ref,
                ids,
            )
        )

        out.append(
            self._req(
                "amt_over_500",
                txn.amt > 500,
                f"The amount is ${txn.amt:,.2f}"
                + (", above the $500 band." if txn.amt > 500 else ", under the $500 band."),
                ref,
                ids,
            )
        )
        out.append(
            self._req(
                "amt_under_5",
                txn.amt < 5,
                f"The amount is ${txn.amt:,.2f}"
                + (
                    ", the sub-$5 size a card-testing probe uses."
                    if txn.amt < 5
                    else ", not the sub-$5 size a probe authorisation uses."
                ),
                ref,
                ids,
            )
        )

        # addr2 is the billing country; blank means the column was absent, which
        # is not the same as "not home".
        if txn.addr2:
            not_home = txn.addr2 != HOME_COUNTRY
            out.append(
                self._req(
                    "country_not_home",
                    not_home,
                    (
                        f"The billing country is {txn.addr2}, not the cardholder's home country."
                        if not_home
                        else "The billing country is the cardholder's home country."
                    ),
                    ref,
                    ids,
                )
            )

        out.extend(self._match_flags(txn.m_feats, ref, ids))
        out.extend(self._distance(txn.dist1, ref, ids))
        return self._record(out)

    # ── identity ─────────────────────────────────────────────────────────────

    def from_device_novelty(
        self, device: DeviceNovelty, ref: str, txn_id: str
    ) -> list[PostingRequest]:
        """Identity flags, and whether this card has used this device before.

        Everything here is gated on an identity record existing. A card-present
        purchase has none, and ``prior_txns_this_device_on_card == 0`` then means
        *not observable*, not *new device*.
        """
        flags = device.flags
        if flags is None or not flags.device_key:
            return []

        ids = (txn_id, flags.device_key)
        out = [
            self._req(
                "device_new",
                flags.device_new == "New",
                (
                    "The identity record marks this device as new for the account."
                    if flags.device_new == "New"
                    else "The identity record does not mark this device as new for the account."
                ),
                ref,
                ids,
            ),
            self._req(
                "device_found",
                flags.device_new == "Found",
                (
                    "The identity record marks this device as previously seen elsewhere."
                    if flags.device_new == "Found"
                    else "The identity record does not carry the 'Found' device state."
                ),
                ref,
                ids,
            ),
            self._req(
                "proxy_present",
                bool(flags.proxy_flag),
                (
                    f"The connection came through a proxy ({flags.proxy_flag})."
                    if flags.proxy_flag
                    else "No proxy was recorded on the connection."
                ),
                ref,
                ids,
            ),
            self._req(
                "device_never_used_on_card",
                device.prior_txns_this_device_on_card == 0,
                (
                    "This card has never been used on this device before."
                    if device.prior_txns_this_device_on_card == 0
                    else (
                        f"This card has used this device "
                        f"{device.prior_txns_this_device_on_card} time(s) before."
                    )
                ),
                ref,
                ids,
            ),
        ]

        if flags.match_status:
            mismatch = "match_status:0" in flags.match_status
            out.append(
                self._req(
                    "match_status_mismatch",
                    mismatch,
                    (
                        "An identity match check failed on this transaction "
                        "(id_34, an unnamed Vesta field)."
                        if mismatch
                        else "The identity match checks on this transaction did not fail."
                    ),
                    ref,
                    ids,
                )
            )
        return self._record(out)

    def from_ring(self, ring: RingExpansion, ref: str, card_id: str) -> list[PostingRequest]:
        """Whether the fingerprint on this card is specific or a browser class.

        A profile spanning more than twenty cards is a fingerprint class, not a
        device: 116 of the 9,704 profiles carry 24,653 of the card links and the
        largest spans 842 cards.
        """
        if not ring.devices_used:
            return []
        widest = max(device.n_cards for device in ring.devices_used)
        generic = widest > GENERIC_DEVICE_CARDS
        return self._record(
            [
                self._req(
                    "device_profile_generic",
                    generic,
                    (
                        f"The widest device profile on this card spans {widest} cards, so it "
                        "identifies a browser configuration rather than a device."
                        if generic
                        else (
                            f"The device profiles on this card span at most {widest} cards, "
                            "specific enough to link them meaningfully."
                        )
                    ),
                    ref,
                    (card_id,),
                )
            ]
        )

    # ── the card's own history ───────────────────────────────────────────────

    def from_amount_band(
        self,
        band: AmountBandProbe,
        stats: CardAmountStats | None,
        ref: str,
        card_id: str,
        amt: float,
    ) -> list[PostingRequest]:
        """How this amount sits against what the card has actually been charged."""
        ids = (card_id,)
        out = [
            self._req(
                "amt_seen_before_on_card",
                band.prior_charges_in_band >= 1,
                (
                    f"{band.prior_charges_in_band} prior charges on this card fall within "
                    f"$0.50 of ${amt:,.2f}."
                    if band.prior_charges_in_band >= 1
                    else f"This card has never been charged near ${amt:,.2f} before."
                ),
                ref,
                ids,
            )
        ]

        # The mean and standard deviation comparisons need a baseline. Below ten
        # prior transactions a card has none, and the fit's own predicates say so.
        if stats is not None and stats.n_prior >= MIN_PRIOR_FOR_BASELINE:
            mean = stats.mean
            std = stats.stddev
            if mean is not None:
                out.append(
                    self._req(
                        "amt_below_prior_mean",
                        amt < mean,
                        (
                            f"${amt:,.2f} is below this card's own prior mean of ${mean:,.2f}."
                            if amt < mean
                            else f"${amt:,.2f} is above this card's prior mean of ${mean:,.2f}."
                        ),
                        ref,
                        ids,
                    )
                )
            if mean is not None and std is not None:
                threshold = mean + 2 * std
                out.append(
                    self._req(
                        "amt_above_prior_p95ish",
                        amt > threshold,
                        (
                            f"${amt:,.2f} is more than two standard deviations above this "
                            f"card's prior mean (${threshold:,.2f})."
                            if amt > threshold
                            else (
                                f"${amt:,.2f} is within two standard deviations of this card's "
                                f"prior mean (${threshold:,.2f})."
                            )
                        ),
                        ref,
                        ids,
                    )
                )
        return self._record(out)

    def from_region_novelty(
        self, region: RegionNovelty, ref: str, card_id: str, region_code: str
    ) -> list[PostingRequest]:
        """Region novelty as of the alert, and the trip-versus-clone check."""
        ids = (card_id, region_code) if region_code else (card_id,)
        out: list[PostingRequest] = []

        if region.prior_txns_on_card == 0:
            out.append(
                self._req(
                    "first_txn_on_card",
                    True,
                    "This is the first transaction ever recorded on this card.",
                    ref,
                    ids,
                )
            )
            return self._record(out)

        out.append(self._req("first_txn_on_card", False, "The card has prior history.", ref, ids))

        if region_code and region.prior_txns_on_card >= MIN_PRIOR_FOR_BASELINE:
            novel = region.prior_txns_in_region == 0
            out.append(
                self._req(
                    "region_novel",
                    novel,
                    (
                        f"The card had never been used in billing region {region_code} before "
                        "this transaction."
                        if novel
                        else (
                            f"The card had {region.prior_txns_in_region} prior transactions in "
                            f"billing region {region_code}."
                        )
                    ),
                    ref,
                    ids,
                )
            )
        out.append(
            self._req(
                "region_well_established",
                region.prior_txns_in_region >= MIN_PRIOR_FOR_BASELINE,
                (
                    f"Billing region {region_code} is well established for this card "
                    f"({region.prior_txns_in_region} prior transactions"
                    + (
                        f", first seen {region.first_seen_in_region})."
                        if region.first_seen_in_region
                        else ")."
                    )
                    if region.prior_txns_in_region >= MIN_PRIOR_FOR_BASELINE
                    else (
                        f"The card has only {region.prior_txns_in_region} prior transactions "
                        f"in billing region {region_code}."
                    )
                ),
                ref,
                ids,
            )
        )

        # A cardholder cannot be in two billing regions at once. Fitted at LR 0.518
        # — mild evidence AGAINST fraud, which is counter-intuitive and is kept as
        # fitted rather than overridden; see docs/system/PRD.md open question Q4.
        elsewhere = region.in_person_elsewhere_24h > 0
        out.append(
            self._req(
                "in_person_elsewhere_same_day",
                elsewhere,
                (
                    f"The card was used in person in {region.in_person_elsewhere_24h} "
                    "transaction(s) in a different billing region within a day of this one."
                    if elsewhere
                    else "No card-present activity in another billing region within a day."
                ),
                ref,
                ids,
            )
        )
        return self._record(out)

    def from_product_novelty(
        self, product: ProductNovelty, ref: str, card_id: str, code: str
    ) -> list[PostingRequest]:
        if product.prior_txns_on_card < MIN_PRIOR_FOR_BASELINE:
            return []
        novel = product.prior_txns_this_product == 0
        return self._record(
            [
                self._req(
                    "product_novel_for_card",
                    novel,
                    (
                        f"The card had never been used for product code {code} before."
                        if novel
                        else (
                            f"The card has {product.prior_txns_this_product} prior purchases "
                            f"of product code {code}."
                        )
                    ),
                    ref,
                    (card_id,),
                )
            ]
        )

    def from_sequence(
        self, sequence: TxnSequenceContext, ref: str, txn_id: str
    ) -> list[PostingRequest]:
        """Burst timing, from the NEXT edges no v1 query ever traversed."""
        if not sequence.has_prev or sequence.seconds_since_prev is None:
            return []
        gap = sequence.seconds_since_prev
        ids = (txn_id,)
        return self._record(
            [
                self._req(
                    "burst_under_1h",
                    gap < 3600,
                    (
                        f"The previous transaction on this card was {gap / 60:.0f} minutes earlier."
                        if gap < 3600
                        else f"The previous transaction was {gap / 3600:.1f} hours earlier."
                    ),
                    ref,
                    ids,
                ),
                self._req(
                    "burst_under_10m",
                    gap < 600,
                    (
                        f"The previous transaction was {gap / 60:.0f} minutes earlier, "
                        "inside the ten-minute burst window."
                        if gap < 600
                        else "The previous transaction was more than ten minutes earlier."
                    ),
                    ref,
                    ids,
                ),
            ]
        )

    # ── internals ────────────────────────────────────────────────────────────

    def _record(self, requests: Sequence[PostingRequest]) -> list[PostingRequest]:
        self.requests.extend(requests)
        return list(requests)

    def _req(
        self,
        feature: str,
        present: bool,
        claim: str,
        ref: str,
        entity_ids: Iterable[str] = (),
        source: EvidenceSource = EvidenceSource.GRAPH,
    ) -> PostingRequest:
        if not self.table.has(feature):
            # A typo here would post nothing and change the probability silently.
            raise KeyError(f"'{feature}' is not in the fitted likelihood table")
        return PostingRequest(feature, present, claim, ref, tuple(entity_ids), source)

    @staticmethod
    def _risk_band(score: float) -> tuple[str, str]:
        """Exactly one band is posted. The other four are the same fact restated."""
        if score < 0.30:
            return "risk_00_30", "in its lowest band"
        if score < 0.50:
            return "risk_30_50", "in its second band"
        if score < 0.70:
            return "risk_50_70", "in its middle band"
        if score < 0.85:
            return "risk_70_85", "in its second-highest band"
        return "risk_85_100", "in its highest band"

    def _match_flags(self, m_feats: str, ref: str, ids: tuple[str, ...]) -> list[PostingRequest]:
        """M1–M9 are unnamed Vesta match flags; the claim says so rather than guessing."""
        if not m_feats:
            return []
        any_false = "F" in m_feats.split("|")
        # Mutually exclusive by construction, so only the true one is posted.
        if any_false:
            return [
                self._req(
                    "m_flags_any_false",
                    True,
                    "At least one of the card's unnamed match flags (M1-M9) is false.",
                    ref,
                    ids,
                )
            ]
        return [
            self._req(
                "m_flags_all_true",
                True,
                "Every one of the card's unnamed match flags (M1-M9) is true.",
                ref,
                ids,
            )
        ]

    def _distance(
        self, dist1: float | None, ref: str, ids: tuple[str, ...]
    ) -> list[PostingRequest]:
        """`dist1` is -1 when absent, which is a fitted feature in its own right."""
        if dist1 is None:
            return []
        if dist1 < 0:
            return [
                self._req(
                    "dist1_missing",
                    True,
                    "The transaction carries no distance value (an unnamed Vesta field).",
                    ref,
                    ids,
                )
            ]
        return [
            self._req(
                "dist1_missing", False, "The transaction carries a distance value.", ref, ids
            ),
            self._req(
                "dist1_large",
                dist1 > 100,
                (
                    f"The transaction's distance value is {dist1:,.0f}, a large one."
                    if dist1 > 100
                    else f"The transaction's distance value is {dist1:,.0f}, a small one."
                ),
                ref,
                ids,
            ),
        ]


def mean_and_stddev(n: int, total: float, total_sq: float) -> tuple[float | None, float | None]:
    """Sample mean and standard deviation from the sums the graph returns.

    ``card_amount_stats`` returns raw sums rather than a mean so the caller can
    see the sample size: a mean over four prior transactions is not a baseline.
    """
    if n <= 0:
        return None, None
    mean = total / n
    if n < 2:
        return mean, None
    variance = max((total_sq - n * mean * mean) / (n - 1), 0.0)
    return mean, math.sqrt(variance)
