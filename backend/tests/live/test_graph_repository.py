"""The live regression: every number here was verified by hand against the graph.

Ported from ``scripts/test_tools.py``, which ran 34 assertions across 13 tool
calls with a bespoke ``check()`` harness and a module-global failure list. The
assertions are unchanged; only the harness is.

These hit the real Savanna workspace, so they are marked ``live`` and excluded
from CI with ``-m "not live"``. Run them before a push:

    pytest backend/tests -m live -q

The workspace auto-stops and takes about 45 seconds to wake. The repository's
cold-start retry absorbs that, so the first test in the module may be slow
rather than failing.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from sentinel.config.settings import Settings, get_settings
from sentinel.graph.tigergraph import TigerGraphRestRepository
from sentinel.tools import QueryLog, ToolRegistry

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]

# The HHG-003 subject: a $49.00 card-present charge the cardholder disputes.
CARD = "C08623-K2"
CUSTOMER = "C08623"
TXN = "3530164"
TS = "2016-12-10 13:01:21"
REGION = "330.0"


def _settings() -> Settings:
    """Build settings from the TigerGraph half of the configuration only.

    Read through ``Settings``, not ``os.environ``. The credentials live in
    ``.env`` and pytest does not put that file into the environment, so a
    direct ``os.environ`` read found nothing and every test here skipped —
    and "16 skipped" reads, at a glance, exactly like "16 passed".

    The OpenAI variables are required by ``Settings`` and are not needed here,
    so they are filled with placeholders rather than making a graph test
    depend on a model credential.
    """
    try:
        configured = get_settings()
    except Exception as exc:  # noqa: BLE001 - pydantic-settings raises several
        pytest.skip(f"configuration is incomplete ({exc}); live tests need the workspace")
    if not configured.tg_host or not configured.tg_secret.get_secret_value():
        pytest.skip("TG_HOST and TG_SECRET are not configured; live tests need the workspace")
    return Settings(  # type: ignore[call-arg]
        tg_host=configured.tg_host,
        tg_secret=configured.tg_secret,
        tg_graph=configured.tg_graph,
        tg_api_token=configured.tg_api_token,
        openai_api_key="not-used-by-graph-tests",
        openai_model="not-used-by-graph-tests",
        openai_embedding_model="not-used-by-graph-tests",
    )


@pytest_asyncio.fixture(scope="module")
async def repo():
    repository = TigerGraphRestRepository.from_settings(_settings())
    try:
        yield repository
    finally:
        await repository.aclose()


@pytest_asyncio.fixture(scope="module")
async def tools(repo):
    return ToolRegistry(repo, QueryLog())


async def call(tools, name: str, **args):
    result = await tools.call(name, args)
    assert result.ok, f"{name} failed: {result.error}"
    return result.data


# ── the graph is the one we loaded ──────────────────────────────────────────


async def test_the_vertex_census_matches_the_load(repo):
    counts = await repo.stat_vertex_counts()
    assert counts["Transaction"] == 590_742
    assert counts["Card"] == 14_317
    assert counts["Customer"] == 13_553
    assert counts["ClosedCase"] == 5_565
    assert counts["DeviceProfile"] == 9_704
    assert counts["Alert"] == 20


# ── the alert itself ────────────────────────────────────────────────────────


async def test_txn_detail_returns_the_flagged_transaction(tools):
    txn = await call(tools, "txn_detail", txn_id=TXN)
    assert txn.txn.amt == 49.0
    assert txn.txn.channel == "in_person"
    assert txn.txn.risk_score == 0.4
    assert txn.txn.addr1 == REGION
    # No identity record exists for a card-present purchase. That is "not
    # observable", not "new device" — the distinction nine of the 20 alerts need.
    assert txn.txn.device_key == ""


async def test_card_baseline_is_what_normal_looks_like(tools):
    base = await call(tools, "card_baseline", card_id=CARD)
    assert base.card.n_txns == 1134
    assert base.card.n_in_person == 1080
    assert base.card.median_amt == 68.01
    assert base.card.p95_amt == 425.66


# ── the exonerating evidence ────────────────────────────────────────────────


async def test_the_billing_region_is_long_established_for_this_card(tools):
    region = await call(tools, "region_novelty", card_id=CARD, region=REGION, as_of=TS)
    assert region.prior_txns_in_region == 42
    assert region.prior_txns_on_card == 980
    assert str(region.first_seen_in_region).startswith("2016-07-09")


async def test_the_amount_is_ordinary_for_this_card(tools):
    band = await call(tools, "amount_band_probe", card_id=CARD, amt=49.0, tol=0.5, as_of=TS)
    assert band.prior_charges_in_band == 53


async def test_the_charge_recurs_monthly(tools):
    recurring = await call(tools, "recurring_charge_probe", card_id=CARD, amt=49.0, product="W")
    assert recurring.matching_charges >= 50
    assert recurring.distinct_months >= 5


# ── the neighbourhood ───────────────────────────────────────────────────────


async def test_the_72_hour_window_holds_43_transactions(tools):
    window = await call(tools, "card_window", card_id=CARD, center=TS, hours=72)
    assert len(window.window) == 43
    riskiest = max(window.window, key=lambda row: row.risk_score)
    assert riskiest.txn_id == "3530056"
    assert riskiest.amt == 116.93


async def test_no_card_testing_sequence_precedes_the_alert(tools):
    probe = await call(tools, "card_testing_probe", card_id=CARD, center=TS, small_amt=5.0)
    assert probe.small_online_auths_1h == 0


async def test_this_card_never_used_this_device(tools):
    # The flagged transaction has no device record at all, so the count is 0 and
    # means "not observable".
    device = await call(tools, "device_novelty", txn_id=TXN)
    assert device.prior_txns_this_device_on_card == 0


# ── the history that cuts the other way ─────────────────────────────────────


async def test_this_cardholder_has_denied_activity_before_and_been_right(tools):
    history = await call(tools, "customer_case_history", customer_id=CUSTOMER)
    assert history.confirmed_fraud_cases == 5
    assert history.cleared_cases == 1
    assert history.customer_reports_confirmed_fraud == 5


async def test_the_region_is_not_hot_against_a_matched_baseline(tools):
    # 18 of 608 looks alarming until the same window a month earlier shows 13 of 552.
    cluster = await call(
        tools,
        "region_cluster",
        region=REGION,
        # Inclusive end bounds: 00:00:00 would drop a whole day from each window
        # and quietly change the comparison this test exists to pin.
        win_from="2016-12-08 00:00:00",
        win_to="2016-12-12 23:59:59",
        base_from="2016-11-08 00:00:00",
        base_to="2016-11-12 23:59:59",
        risk_thresh=0.7,
    )
    assert cluster.window_txns == 608
    assert cluster.window_high_risk == 18
    assert cluster.baseline_txns == 552
    assert cluster.baseline_high_risk == 13


# ── the device-specificity gate ─────────────────────────────────────────────


async def test_the_ring_gate_is_what_stops_a_phantom_ring(tools):
    gated = await call(tools, "ring_expand", card_id=CARD, center=TS, days=30, max_device_cards=20)
    assert gated.devices_total == 5
    assert gated.devices_specific_enough == 2
    assert len(gated.connected_cards) == 11

    ungated = await call(
        tools, "ring_expand", card_id=CARD, center=TS, days=30, max_device_cards=10_000
    )
    # 25 phantom cards, every one of which would feed R6 -> CREATE_CASE + FILE_REPORT.
    assert len(ungated.connected_cards) == 25


# ── memory ──────────────────────────────────────────────────────────────────


async def test_the_card_already_carries_six_bank_cases_and_one_of_ours(tools):
    memory = await call(tools, "case_memory_for_card", card_id=CARD)
    assert len(memory.bank_closed_cases) == 6
    assert len(memory.sentinel_cases) == 1


# ── the v2 queries ──────────────────────────────────────────────────────────


async def test_the_next_edges_are_finally_readable(repo):
    # 576,425 NEXT edges were loaded and no v1 query traversed them. These feed
    # burst_under_1h and burst_under_10m, two fitted features with no source.
    raw = await repo.run_query("txn_sequence_context", {"t_in": TXN})
    assert raw["has_prev"] == 1
    assert raw["seconds_since_prev"] == 2920  # 48.7 minutes: inside the 1h burst band


async def test_amount_statistics_are_available_before_a_date(repo):
    raw = await repo.run_query("card_amount_stats", {"c_in": CARD, "as_of": TS})
    assert raw["n_prior"] == 980
    mean = raw["sum_amt"] / raw["n_prior"]
    assert 128.0 < mean < 130.0
    # $49.00 sits below this card's own mean — evidence for legitimacy, not against.
    assert mean > 49.0


async def test_the_alert_queue_returns_all_twenty(repo):
    raw = await repo.run_query("alerts_queue", {})
    assert len(raw["alerts"]) == 20
