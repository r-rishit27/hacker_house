"""One typed result per installed GSQL query.

Field names are the ``PRINT ... AS`` aliases in
``graph/queries/sentinel_queries.gsql``, read off the real file rather than
guessed. A field is optional here for one of two reasons: either the query
projects a subset of the vertex's attributes, or the value comes from an
accumulator that can be empty — ``MaxAccum<DOUBLE>`` yields ``-1.797e308`` and
``MinAccum<DATETIME>`` yields ``1970-01-01`` when nothing accumulated, and the
repository's ``ResponseNormalizer`` has already turned both into ``None``.
Typing those fields ``| None`` is what stops an LLM reporting that a card was
first seen in a region in 1970.

Four facts about these queries are encoded as properties rather than left for
each caller to remember; each is marked where it appears.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, model_validator

# ── normalisation helpers ────────────────────────────────────────────────────


def _strip_alias(key: str) -> str:
    """``T.txn_id`` -> ``txn_id``; ``C2.@shared`` -> ``shared``."""
    return key.rsplit(".", 1)[-1].lstrip("@")


def _flatten_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Unwrap a TigerGraph vertex row and drop its alias prefixes.

    The repository's normalizer does this on the way out; repeating it here is
    what lets a DTO be built straight from a recorded raw payload in a fixture
    or a replay.
    """
    attributes = row.get("attributes")
    if isinstance(attributes, Mapping):
        merged = {k: v for k, v in row.items() if k not in ("attributes", "v_id", "v_type")}
        merged.update(attributes)
        row = merged
    return {_strip_alias(k): v for k, v in row.items()}


def _first(value: Any) -> Any:
    """These aliases print a vertex set that holds at most one row."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _scalar_from_rows(key: str) -> Callable[[Any], Any]:
    """``PRINT R[R.n_cards] AS x`` prints a row list, not a number."""

    def pick(value: Any) -> Any:
        row = _first(value)
        if isinstance(row, Mapping):
            return _flatten_row(row).get(key)
        return row

    return pick


class GraphModel(BaseModel):
    """Base for every graph result: immutable, alias-stripped, extras dropped."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return _flatten_row(value)
        return value


#: A vertex-set alias that carries at most one row.
OneRow = BeforeValidator(_first)


# ── vertex rows ──────────────────────────────────────────────────────────────


class TransactionRow(GraphModel):
    """A ``Transaction``. Only ``txn_id`` is guaranteed; the rest depends on the
    projection the query used."""

    txn_id: str
    card_id: str = ""
    customer_id: str = ""
    ts: datetime | None = None
    txn_dt: int | None = None
    amt: float = 0.0
    product: str = ""
    channel: str = ""
    risk_score: float = 0.0
    addr1: str = ""
    addr2: str = ""
    dist1: float | None = None
    dist2: float | None = None
    p_email: str = ""
    r_email: str = ""
    device_key: str = ""
    device_new: str = ""
    proxy_flag: str = ""
    match_status: str = ""
    c_feats: str = ""
    d_feats: str = ""
    m_feats: str = ""
    v_feats: str = ""

    @property
    def device_observable(self) -> bool:
        """No identity record means no device evidence — not an absent device."""
        return bool(self.device_key)


class CardRow(GraphModel):
    """A ``Card``. ``median_amt``/``p95_amt``/``max_amt`` are the baseline the
    amount probes are read against."""

    card_id: str
    customer_id: str = ""
    card1: str = ""
    card2: str = ""
    card3: str = ""
    card5: str = ""
    network: str = ""
    card_type: str = ""
    home_region: str = ""
    home_country: str = ""
    n_txns: int = 0
    n_online: int = 0
    n_in_person: int = 0
    median_amt: float | None = None
    p95_amt: float | None = None
    max_amt: float | None = None
    n_regions: int = 0
    n_products: int = 0
    top_products: str = ""
    first_seen: datetime | None = None
    last_seen: datetime | None = None


class CustomerRow(GraphModel):
    customer_id: str
    n_cards: int = 0
    n_txns: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None


class DeviceProfileRow(GraphModel):
    """A ``DeviceProfile``. ``n_cards`` is the profile's specificity: a profile
    spanning hundreds of cards is a browser fingerprint and links nobody."""

    device_key: str
    label: str = ""
    device_info: str = ""
    device_type: str = ""
    os: str = ""
    browser: str = ""
    screen: str = ""
    n_txns: int = 0
    n_cards: int = 0
    n_customers: int = 0


class SiblingCardRow(GraphModel):
    """One row of ``card_baseline.sibling_cards``."""

    card_id: str
    card_type: str = ""
    network: str = ""
    n_txns: int = 0
    median_amt: float | None = None


class CardOnDeviceRow(GraphModel):
    """One row of ``device_neighbors.cards_on_device``."""

    card_id: str
    customer_id: str = ""
    hits: int = 0
    max_risk: float | None = None
    amount: float = 0.0


class ConnectedCardRow(GraphModel):
    """One row of ``ring_expand.connected_cards``."""

    card_id: str
    customer_id: str = ""
    shared: int = 0
    max_risk: float | None = None
    via_device: list[str] = []


class DeviceLinkRow(GraphModel):
    """One row of ``ring_expand.devices_used`` or ``.identifying_devices``."""

    device_key: str
    label: str = ""
    n_cards: int = 0
    n_customers: int = 0


class TxnFlagsRow(GraphModel):
    """``device_novelty.flags`` — id_15, id_23 and id_34 on the alert itself."""

    txn_id: str
    device_key: str = ""
    device_new: str = ""
    proxy_flag: str = ""
    match_status: str = ""
    channel: str = ""
    product: str = ""


class CardAmountsRow(GraphModel):
    """``amount_band_probe.card_amounts``."""

    median_amt: float | None = None
    p95_amt: float | None = None
    max_amt: float | None = None


class ClosedCaseRow(GraphModel):
    """A ``ClosedCase``: the bank's own labelled history. Three queries project
    different subsets of it."""

    case_id: str
    customer_id: str = ""
    card_id: str = ""
    outcome: str = ""
    pattern: str = ""
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    exposure_usd: float = 0.0
    n_txns: int = 0
    actions_taken: str = ""
    report_filed: str = ""
    analyst_notes: str = ""

    @property
    def confirmed_fraud(self) -> bool:
        return self.outcome == "confirmed_fraud"


class SentinelCaseRow(GraphModel):
    """A ``FraudCase`` — a case Sentinel itself wrote. Retrieving one is the
    memory-compounds mechanism."""

    case_id: str
    source_alert: str = ""
    verdict: str = ""
    fraud_probability: float | None = None
    status: str = ""
    pattern: str = ""
    exposure_usd: float = 0.0
    final_actions: str = ""
    summary: str = ""


# ── query results ────────────────────────────────────────────────────────────


class TxnDetail(GraphModel):
    """``txn_detail(t_in)`` — the alert itself.

    The only source of the true ``ts``; every other query's ``center``/``as_of``
    is derived from it. ``device`` is absent for the seven of twenty alerts with
    no identity record.
    """

    txn: Annotated[TransactionRow, OneRow]
    device: Annotated[DeviceProfileRow | None, OneRow] = None
    card: Annotated[CardRow | None, OneRow] = None


class CardBaseline(GraphModel):
    """``card_baseline(c_in)`` — what normal looks like for this cardholder."""

    card: Annotated[CardRow, OneRow]
    customer: Annotated[CustomerRow | None, OneRow] = None
    sibling_cards: list[SiblingCardRow] = []

    @property
    def other_cards(self) -> list[SiblingCardRow]:
        """``sibling_cards`` includes the seed card, because the traversal goes
        card -> customer -> cards. Counting BLOCK_ALL_CARDS off the raw list is
        off by one every time."""
        return [row for row in self.sibling_cards if row.card_id != self.card.card_id]

    @property
    def n_other_cards(self) -> int:
        return len(self.other_cards)


class CardWindow(GraphModel):
    """``card_window(c_in, center, hours)`` — the alert's neighbourhood in time,
    ordered by ``ts`` ascending."""

    window: list[TransactionRow] = []

    @property
    def txn_ids(self) -> list[str]:
        return [row.txn_id for row in self.window]


class CardTestingProbe(GraphModel):
    """``card_testing_probe(c_in, center, small_amt)`` — the R5 shape: small
    online authorisations followed by a large purchase.

    ``largest_purchase_after`` is ``None`` when nothing followed; the threshold
    that makes this card testing lives in the policy engine, not here.
    """

    small_online_auths_1h: int = 0
    small_online_auths_24h: int = 0
    small_txn_ids: list[str] = []
    largest_purchase_after: float | None = None
    purchases_after_ids: list[str] = []


class RegionNovelty(GraphModel):
    """``region_novelty(c_in, region, as_of)`` — novelty evaluated as of the
    alert, never over the whole file.

    ``in_person_elsewhere_24h`` is the trip-versus-clone discriminator: a
    cardholder cannot be in two billing regions at once.
    """

    prior_txns_in_region: int = 0
    total_txns_in_region: int = 0
    prior_txns_on_card: int = 0
    first_seen_in_region: datetime | None = None
    last_in_region_before_alert: datetime | None = None
    in_person_elsewhere_24h: int = 0
    other_regions_24h: list[str] = []


class AmountBandProbe(GraphModel):
    """``amount_band_probe(c_in, amt, tol, as_of)`` — is this amount ordinary
    for this card. The strongest exonerating signal in the hand investigation."""

    prior_charges_in_band: int = 0
    total_charges_in_band: int = 0
    prior_txns_on_card: int = 0
    card_amounts: Annotated[CardAmountsRow | None, OneRow] = None


class DeviceNovelty(GraphModel):
    """``device_novelty(t_in)`` — has this card used this device before.

    Zero prior transactions on a device whose ``device_key`` is ``""`` means the
    transaction has no identity record at all, which is NOT the same claim as "a
    new device". Seven of the twenty alerts are in that state, and reading them
    as new devices manufactures fraud out of missing data. Ask ``observable``
    before reading ``prior_txns_this_device_on_card``.
    """

    flags: Annotated[TxnFlagsRow | None, OneRow] = None
    prior_txns_this_device_on_card: int = 0
    prior_online_txns_on_card: int = 0
    prior_txns_on_card: int = 0

    @property
    def observable(self) -> bool:
        return bool(self.flags is not None and self.flags.device_key)

    @property
    def device_is_new_to_card(self) -> bool | None:
        """``None`` means not observable — never coerce it to ``True``."""
        if not self.observable:
            return None
        return self.prior_txns_this_device_on_card == 0


class DeviceNeighbors(GraphModel):
    """``device_neighbors(d_in, center, days)`` — who else used this
    fingerprint, and how specific the fingerprint is."""

    device: Annotated[DeviceProfileRow | None, OneRow] = None
    profile_spans_cards: Annotated[int, BeforeValidator(_scalar_from_rows("n_cards"))] = 0
    txns_in_window: int = 0
    cards_on_device: list[CardOnDeviceRow] = []

    @property
    def other_card_ids(self) -> list[str]:
        return [row.card_id for row in self.cards_on_device]


class RegionCluster(GraphModel):
    """``region_cluster(r_in, win_from, win_to, base_from, base_to, risk_thresh)``
    — always against a matched baseline window.

    18 high-risk of 608 looks alarming until the same window a month earlier
    shows 13 of 552, so the rates, not the counts, are the finding.
    """

    window_txns: int = 0
    window_high_risk: int = 0
    baseline_txns: int = 0
    baseline_high_risk: int = 0
    window_high_risk_cards: list[str] = []
    window_high_risk_txns: list[str] = []

    @property
    def window_rate(self) -> float | None:
        return self.window_high_risk / self.window_txns if self.window_txns else None

    @property
    def baseline_rate(self) -> float | None:
        return self.baseline_high_risk / self.baseline_txns if self.baseline_txns else None

    @property
    def lift(self) -> float | None:
        """``None`` when either window is empty or the baseline rate is zero —
        an undefined lift is not a large one."""
        window, baseline = self.window_rate, self.baseline_rate
        if window is None or not baseline:
            return None
        return window / baseline


class EmailCluster(GraphModel):
    """``email_cluster(e_in, center, days, risk_thresh)`` — recipient fan-in.

    This keys on an email DOMAIN, not an address: ``gmail.com`` at +/-30 days
    returns 12,018 transactions across 1,837 cards. It is a base rate. It is
    never, on its own, a ring.
    """

    txns_in_window: int = 0
    high_risk_in_window: int = 0
    distinct_cards: int = 0

    @property
    def high_risk_rate(self) -> float | None:
        return self.high_risk_in_window / self.txns_in_window if self.txns_in_window else None


class RingExpansion(GraphModel):
    """``ring_expand(c_in, center, days, max_device_cards)`` — cards linked by a
    SPECIFIC shared device.

    ``devices_total`` minus ``devices_specific_enough`` is how many generic
    fingerprints the gate discarded; a large difference is the query telling you
    the online footprint is unremarkable.
    """

    devices_used: list[DeviceLinkRow] = []
    identifying_devices: list[DeviceLinkRow] = []
    devices_total: int = 0
    devices_specific_enough: int = 0
    connected_cards: list[ConnectedCardRow] = []

    @property
    def connected_card_ids(self) -> list[str]:
        return [row.card_id for row in self.connected_cards]


class RecurringChargeProbe(GraphModel):
    """``recurring_charge_probe(c_in, amt, tol, product)`` — R7: is the disputed
    charge a subscription the cardholder forgot."""

    matching_charges: int = 0
    distinct_months: int = 0
    months: list[str] = []
    regions: list[str] = []
    txn_ids: list[str] = []


class VelocityProbe(GraphModel):
    """``velocity_probe(c_in, center, hours)`` — burst, geography and device
    churn in one window."""

    txns_in_window: int = 0
    online_in_window: int = 0
    total_amount: float = 0.0
    max_risk_in_window: float | None = None
    distinct_regions: int = 0
    distinct_devices: int = 0
    regions: list[str] = []


class CustomerCaseHistory(GraphModel):
    """``customer_case_history(customer_id)`` — this customer's closed cases,
    including how often their own denials were later confirmed."""

    cases: list[ClosedCaseRow] = []
    confirmed_fraud_cases: int = 0
    cleared_cases: int = 0
    customer_reports_confirmed_fraud: int = 0
    lifetime_exposure: float = 0.0
    patterns_seen: list[str] = []


class SimilarPriorCases(GraphModel):
    """``similar_prior_cases(pattern_in, amt_lo, amt_hi, outcome_in, k)``.

    The GSQL is ``ORDER BY exposure_usd ASC LIMIT k``, so this returns the k
    CHEAPEST cases in the exposure band, not the k most similar ones. Treat it
    as a structural filter over the 5,565 closed cases; similarity is GraphRAG's
    job and this query is precisely the hole it fills.
    """

    cases: list[ClosedCaseRow] = []


class CardCaseMemory(GraphModel):
    """``case_memory_for_card(c_in)`` — everything already known about this card:
    the bank's closed cases and the cases Sentinel wrote earlier in the run."""

    bank_closed_cases: list[ClosedCaseRow] = []
    sentinel_cases: list[SentinelCaseRow] = []
    cases_connecting_this_card: list[SentinelCaseRow] = []


# ── v2 queries: the three fitted features that had no tool behind them ───────


class TxnSequenceContext(GraphModel):
    """``txn_sequence_context(t_in)`` — the gap to the neighbouring transactions.

    576,425 ``NEXT`` edges were loaded and no v1 query traversed them, so
    ``burst_under_1h`` and ``burst_under_10m`` had nothing to read. Each
    transaction has at most one neighbour in each direction within its card, so
    ``has_prev`` disambiguates a real zero gap from an absent neighbour.
    """

    has_prev: int = 0
    has_next: int = 0
    seconds_since_prev: int | None = None
    seconds_to_next: int | None = None
    prev_txn: Annotated[TransactionRow | None, OneRow] = None
    next_txn: Annotated[TransactionRow | None, OneRow] = None

    @model_validator(mode="after")
    def _absent_neighbour_has_no_gap(self) -> TxnSequenceContext:
        """A summed gap of 0 with no neighbour is not a zero-second gap."""
        if not self.has_prev:
            object.__setattr__(self, "seconds_since_prev", None)
        if not self.has_next:
            object.__setattr__(self, "seconds_to_next", None)
        return self


class ProductNovelty(GraphModel):
    """``product_novelty(c_in, product, as_of)`` — has this card bought this
    product code before, as of the alert."""

    prior_txns_this_product: int = 0
    total_txns_this_product: int = 0
    prior_txns_on_card: int = 0
    products_used_before: list[str] = []
    first_seen_with_product: datetime | None = None


class CardAmountStats(GraphModel):
    """``card_amount_stats(c_in, as_of)`` — the card's own amount distribution
    before a moment.

    Raw sums rather than a mean, because the sample size decides whether the
    comparison means anything: ``Card`` carries a median and a p95 but no mean
    and no standard deviation, which is why ``amt_above_prior_p95ish`` and
    ``amt_below_prior_mean`` were unreachable in v1.
    """

    n_prior: int = 0
    sum_amt: float = 0.0
    sum_amt_sq: float = 0.0
    max_prior_amt: float | None = None
    min_prior_amt: float | None = None
    card_amounts: Annotated[CardAmountsRow | None, OneRow] = None

    @property
    def mean(self) -> float | None:
        return self.sum_amt / self.n_prior if self.n_prior > 0 else None

    @property
    def stddev(self) -> float | None:
        """Sample standard deviation, or ``None`` below two observations."""
        if self.n_prior < 2:
            return None
        mean = self.sum_amt / self.n_prior
        variance = max((self.sum_amt_sq - self.n_prior * mean * mean) / (self.n_prior - 1), 0.0)
        return math.sqrt(variance)
