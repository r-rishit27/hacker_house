"""Feature extraction: the mapping from graph facts to the 29 fitted features.

A mistake here is invisible. The run completes, the evidence list reads well, and
the probability is simply wrong — no validator can catch it, and the benchmark is
scored against an answer key we do not have. So these tests pin the mapping
against the real HHG-003 numbers and against the three rules the extractor exists
to enforce: exclusive families post once, unobservable is not absent, and absence
is evidence where it was genuinely observed.
"""

from __future__ import annotations

import pytest

from sentinel.domain.enums import EvidenceSource
from sentinel.evidence.extractor import FeatureExtractor, mean_and_stddev
from sentinel.evidence.ledger import EvidenceLedger
from sentinel.evidence.table import DEFAULT_ELT_PATH, EvidenceLikelihoodTable
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

CARD = "C08623-K2"
TXN = "3530164"
REF = "query:txn_detail(txn_id=3530164)"


@pytest.fixture(scope="module")
def table() -> EvidenceLikelihoodTable:
    return EvidenceLikelihoodTable(DEFAULT_ELT_PATH)


@pytest.fixture
def extractor(table: EvidenceLikelihoodTable) -> FeatureExtractor:
    return FeatureExtractor(table)


def txn_detail(**overrides) -> TxnDetail:
    """The real HHG-003 transaction unless a test changes something."""
    txn = {
        "txn_id": TXN,
        "card_id": CARD,
        "customer_id": "C08623",
        "ts": "2016-12-10 13:01:21",
        "amt": 49.0,
        "product": "W",
        "channel": "in_person",
        "risk_score": 0.4,
        "addr1": "330.0",
        "addr2": "87.0",
        "dist1": 9,
        "device_key": "",
        "device_new": "",
        "proxy_flag": "",
        "match_status": "",
        "m_feats": "T|T|T|M0|F|T|F|F|T",
    }
    txn.update(overrides)
    return TxnDetail.model_validate({"txn": [txn], "device": [], "card": []})


def features(requests) -> dict[str, bool]:
    return {r.feature: r.present for r in requests}


# ── exclusive families post once ─────────────────────────────────────────────


def test_exactly_one_risk_band_is_posted(extractor):
    out = extractor.from_txn_detail(txn_detail(risk_score=0.90), REF)
    bands = [r for r in out if r.feature.startswith("risk_")]
    assert len(bands) == 1
    assert bands[0].feature == "risk_85_100"
    assert bands[0].present is True


@pytest.mark.parametrize(
    ("score", "band"),
    [
        (0.05, "risk_00_30"),
        (0.29, "risk_00_30"),
        (0.30, "risk_30_50"),
        (0.40, "risk_30_50"),
        (0.50, "risk_50_70"),
        (0.69, "risk_50_70"),
        (0.70, "risk_70_85"),
        (0.84, "risk_70_85"),
        (0.85, "risk_85_100"),
        (0.90, "risk_85_100"),
    ],
)
def test_every_risk_band_boundary(extractor, score, band):
    out = extractor.from_txn_detail(txn_detail(risk_score=score), REF)
    assert [r.feature for r in out if r.feature.startswith("risk_")] == [band]


def test_the_match_flag_family_posts_one_side_only(extractor):
    any_false = extractor.from_txn_detail(txn_detail(m_feats="T|T|F"), REF)
    assert features(any_false).get("m_flags_any_false") is True
    assert "m_flags_all_true" not in features(any_false)

    all_true = FeatureExtractor(extractor.table).from_txn_detail(txn_detail(m_feats="T|T|T"), REF)
    assert features(all_true).get("m_flags_all_true") is True
    assert "m_flags_any_false" not in features(all_true)


def test_absent_match_flags_post_nothing(extractor):
    out = extractor.from_txn_detail(txn_detail(m_feats=""), REF)
    assert not any(r.feature.startswith("m_flags") for r in out)


# ── unobservable is not absent ───────────────────────────────────────────────


def test_a_card_present_transaction_posts_no_device_features(extractor):
    # Seven of the twenty alerts have no identity record. device_new is then
    # neither present nor absent — posting lr_absent would claim evidence the
    # graph never supplied.
    device = DeviceNovelty.model_validate(
        {
            "flags": [
                {"R.txn_id": TXN, "R.device_key": "", "R.device_new": "", "R.proxy_flag": ""}
            ],
            "prior_txns_this_device_on_card": 0,
            "prior_online_txns_on_card": 0,
            "prior_txns_on_card": 980,
        }
    )
    assert extractor.from_device_novelty(device, REF, TXN) == []


def test_an_online_transaction_posts_the_device_family(extractor):
    device = DeviceNovelty.model_validate(
        {
            "flags": [
                {
                    "R.txn_id": TXN,
                    "R.device_key": "D0e83f0a61762",
                    "R.device_new": "New",
                    "R.proxy_flag": "IP_PROXY:ANONYMOUS",
                    "R.match_status": "match_status:0",
                }
            ],
            "prior_txns_this_device_on_card": 0,
            "prior_online_txns_on_card": 12,
            "prior_txns_on_card": 300,
        }
    )
    got = features(extractor.from_device_novelty(device, REF, TXN))
    assert got["device_new"] is True
    assert got["device_found"] is False
    assert got["proxy_present"] is True
    assert got["device_never_used_on_card"] is True
    assert got["match_status_mismatch"] is True


def test_a_known_device_is_posted_as_exonerating(extractor):
    device = DeviceNovelty.model_validate(
        {
            "flags": [
                {
                    "R.txn_id": TXN,
                    "R.device_key": "D0e83f0a61762",
                    "R.device_new": "Found",
                    "R.proxy_flag": "",
                }
            ],
            "prior_txns_this_device_on_card": 14,
            "prior_online_txns_on_card": 40,
            "prior_txns_on_card": 300,
        }
    )
    got = features(extractor.from_device_novelty(device, REF, TXN))
    assert got["device_new"] is False
    assert got["device_never_used_on_card"] is False
    assert got["proxy_present"] is False


def test_a_missing_billing_country_posts_nothing(extractor):
    # Blank addr2 means the column was absent, which is not "not home".
    out = extractor.from_txn_detail(txn_detail(addr2=""), REF)
    assert "country_not_home" not in features(out)


def test_a_thin_card_gets_no_baseline_comparisons(extractor):
    # Below ten prior transactions there is no baseline, and the offline fit's
    # own predicates say so.
    band = AmountBandProbe(prior_charges_in_band=0, total_charges_in_band=0, prior_txns_on_card=4)
    stats = CardAmountStats(n_prior=4, sum_amt=200.0, sum_amt_sq=12_000.0)
    got = features(extractor.from_amount_band(band, stats, REF, CARD, 49.0))
    assert "amt_below_prior_mean" not in got
    assert "amt_above_prior_p95ish" not in got
    assert "amt_seen_before_on_card" in got, "the band probe needs no baseline"


# ── absence is evidence where it was observed ────────────────────────────────


def test_an_established_region_posts_the_exonerating_side(extractor):
    region = RegionNovelty(
        prior_txns_in_region=42,
        total_txns_in_region=51,
        prior_txns_on_card=980,
        in_person_elsewhere_24h=10,
    )
    got = features(extractor.from_region_novelty(region, REF, CARD, "330.0"))
    assert got["region_novel"] is False
    assert got["region_well_established"] is True
    assert got["first_txn_on_card"] is False
    assert got["in_person_elsewhere_same_day"] is True


def test_a_first_transaction_short_circuits_the_region_family(extractor):
    # first_txn_on_card is LR 5.644. With no history there is nothing to compare
    # a region against, so nothing else in the family is posted.
    region = RegionNovelty(prior_txns_in_region=0, prior_txns_on_card=0)
    got = features(extractor.from_region_novelty(region, REF, CARD, "444.0"))
    assert got == {"first_txn_on_card": True}


def test_the_amount_seen_before_is_the_exonerating_signal(extractor):
    # 53 prior charges within $0.50 of $49.00 — the strongest exonerating signal
    # in the hand investigation.
    band = AmountBandProbe(
        prior_charges_in_band=53, total_charges_in_band=57, prior_txns_on_card=980
    )
    stats = CardAmountStats(n_prior=980, sum_amt=126_261.64, sum_amt_sq=41_956_463.08)
    got = features(extractor.from_amount_band(band, stats, REF, CARD, 49.0))
    assert got["amt_seen_before_on_card"] is True
    assert got["amt_below_prior_mean"] is True, "$49 is below this card's $128.84 mean"
    assert got["amt_above_prior_p95ish"] is False


# ── the v2 features that had no tool in v1 ───────────────────────────────────


def test_burst_timing_comes_from_the_next_edges(extractor):
    # The real gap on HHG-003's flagged transaction, read from the graph.
    sequence = TxnSequenceContext(has_prev=1, seconds_since_prev=2920, has_next=1)
    got = features(extractor.from_sequence(sequence, REF, TXN))
    assert got["burst_under_1h"] is True  # 48.7 minutes
    assert got["burst_under_10m"] is False


def test_no_previous_transaction_posts_no_burst_features(extractor):
    sequence = TxnSequenceContext(has_prev=0)
    assert extractor.from_sequence(sequence, REF, TXN) == []


def test_product_novelty_needs_a_baseline(extractor):
    thin = ProductNovelty(prior_txns_this_product=0, prior_txns_on_card=3)
    assert extractor.from_product_novelty(thin, REF, CARD, "C") == []

    established = ProductNovelty(prior_txns_this_product=0, prior_txns_on_card=980)
    got = features(extractor.from_product_novelty(established, REF, CARD, "C"))
    assert got["product_novel_for_card"] is True


def test_a_generic_device_profile_is_flagged_as_a_browser_class(extractor):
    # The widest profile in the graph spans 842 cards. A link through one of
    # those is a browser configuration, not a shared device.
    ring = RingExpansion.model_validate(
        {
            "devices_used": [
                {
                    "DALL.device_key": "Dc7bc1c5be788",
                    "DALL.label": "Windows | chrome",
                    "DALL.n_cards": 842,
                },
                {"DALL.device_key": "Dabc", "DALL.label": "iPad | safari", "DALL.n_cards": 4},
            ],
            "identifying_devices": [],
            "devices_total": 2,
            "devices_specific_enough": 1,
            "connected_cards": [],
        }
    )
    got = features(extractor.from_ring(ring, REF, CARD))
    assert got["device_profile_generic"] is True


def test_a_specific_device_profile_is_not_flagged(extractor):
    ring = RingExpansion.model_validate(
        {
            "devices_used": [
                {"DALL.device_key": "Dabc", "DALL.label": "iPad | safari", "DALL.n_cards": 4}
            ],
            "identifying_devices": [],
            "devices_total": 1,
            "devices_specific_enough": 1,
            "connected_cards": [],
        }
    )
    assert features(extractor.from_ring(ring, REF, CARD))["device_profile_generic"] is False


# ── the arithmetic and the wiring ────────────────────────────────────────────


def test_mean_and_stddev_reproduce_the_live_card():
    mean, std = mean_and_stddev(980, 126_261.64, 41_956_463.08)
    assert mean is not None and std is not None
    assert round(mean, 2) == 128.84
    assert 161.0 < std < 163.0


def test_mean_and_stddev_refuse_to_invent_a_spread():
    assert mean_and_stddev(0, 0.0, 0.0) == (None, None)
    mean, std = mean_and_stddev(1, 49.0, 2401.0)
    assert mean == 49.0
    assert std is None


def test_a_feature_name_that_is_not_fitted_raises(extractor):
    with pytest.raises(KeyError, match="not in the fitted"):
        extractor._req("not_a_real_feature", True, "claim", REF)


def test_every_request_carries_a_claim_a_ref_and_a_source(extractor):
    out = extractor.from_txn_detail(txn_detail(), REF)
    for request in out:
        assert request.claim.strip(), f"{request.feature} has no claim"
        assert request.ref == REF
        assert request.source is EvidenceSource.GRAPH


def test_extracted_requests_post_cleanly_into_a_ledger(extractor, table):
    # The seam that matters: everything the extractor emits must be postable.
    ledger = EvidenceLedger(table, "customer_report")
    for request in extractor.from_txn_detail(txn_detail(), REF):
        ledger.post(**request.as_kwargs())
    assert ledger.p != ledger.prior
    assert len(ledger.evidence()) == len(extractor.requests)


def test_the_hhg003_facts_move_the_probability_downward(extractor, table):
    """The whole point, end to end: this case should come out well below its trigger prior.

    A customer report starts at 0.75. The graph says the amount is ordinary for
    the card, the region is long established, and there is no identity record to
    incriminate. An agent that cannot reach a low number here blocks legitimate
    customers, which the brief marks down hardest.
    """
    ledger = EvidenceLedger(table, "customer_report")
    for request in extractor.from_txn_detail(txn_detail(), REF):
        ledger.post(**request.as_kwargs())
    region = RegionNovelty(
        prior_txns_in_region=42, prior_txns_on_card=980, in_person_elsewhere_24h=10
    )
    for request in extractor.from_region_novelty(region, REF, CARD, "330.0"):
        ledger.post(**request.as_kwargs())
    band = AmountBandProbe(prior_charges_in_band=53, prior_txns_on_card=980)
    stats = CardAmountStats(n_prior=980, sum_amt=126_261.64, sum_amt_sq=41_956_463.08)
    for request in extractor.from_amount_band(band, stats, REF, CARD, 49.0):
        ledger.post(**request.as_kwargs())

    assert ledger.p < ledger.prior, f"{ledger.p:.3f} should sit below the 0.75 prior"
    assert ledger.independent_support() >= 2
