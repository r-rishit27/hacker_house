"""The tool layer: citations, logging, argument handling and failure shape.

These run against ``FakeGraphRepository``, so they need no credentials and no
workspace. The numbers seeded below are the real ones from the HHG-003 hand
investigation, so a change that breaks the mapping breaks a test.
"""

from __future__ import annotations

import pytest

from sentinel.graph.fake import FakeGraphRepository
from sentinel.tools import CATALOGUE, EvidenceRef, QueryLog, ToolRegistry

CARD = "C08623-K2"
TXN = "3530164"
TS = "2016-12-10 13:01:21"
REGION = "330.0"


def registry(repo: FakeGraphRepository | None = None) -> tuple[ToolRegistry, FakeGraphRepository]:
    repo = repo or FakeGraphRepository()
    return ToolRegistry(repo, QueryLog()), repo


# ── citations ────────────────────────────────────────────────────────────────


def test_the_citation_format_is_the_one_in_the_answer_files():
    # cases/HHG-003.json contains exactly this shape. It is graded output.
    assert (
        EvidenceRef.query("card_baseline", {"card_id": CARD})
        == "query:card_baseline(card_id=C08623-K2)"
    )


def test_a_multi_argument_citation_keeps_its_argument_order():
    ref = EvidenceRef.query("region_novelty", {"card_id": CARD, "region": REGION, "as_of": TS})
    assert ref == f"query:region_novelty(card_id={CARD}, region={REGION}, as_of={TS})"


def test_the_non_query_citation_forms():
    assert EvidenceRef.alert("HHG-003") == "alert:HHG-003"
    assert EvidenceRef.evidence_request(1) == "evidence_request:1"
    assert EvidenceRef.document("POL-R5", "verify_before_block") == (
        "doc:POL-R5#verify_before_block"
    )


# ── the catalogue ────────────────────────────────────────────────────────────


def test_every_investigative_query_is_registered():
    reg, _ = registry()
    expected = {
        # the sixteen from v1
        "txn_detail",
        "card_baseline",
        "card_window",
        "card_testing_probe",
        "region_novelty",
        "amount_band_probe",
        "device_novelty",
        "device_neighbors",
        "region_cluster",
        "email_cluster",
        "ring_expand",
        "recurring_charge_probe",
        "velocity_probe",
        "customer_case_history",
        "similar_prior_cases",
        "case_memory_for_card",
        # v2: the three fitted features that had no tool behind them
        "txn_sequence_context",
        "product_novelty",
        "card_amount_stats",
    }
    assert set(reg.names()) == expected


def test_the_console_read_queries_are_not_offered_to_the_agent():
    # alerts_queue, case_by_id and case_subgraph answer the UI's questions, not
    # the investigation's. An agent given them would spend tool budget on them.
    reg, _ = registry()
    for ui_only in ("alerts_queue", "case_by_id", "case_subgraph", "policy_docs_all"):
        assert ui_only not in reg.names()


def test_every_tool_describes_itself_for_function_calling():
    reg, _ = registry()
    for schema in reg.describe():
        # The planner picks from these descriptions, so an empty one is a bug.
        assert schema["function"]["name"] in reg.names()
        assert schema["function"]["description"].strip()
        assert schema["function"]["parameters"]["type"] == "object"


def test_every_tool_in_the_catalogue_has_a_question_it_answers():
    for tool in CATALOGUE:
        assert tool.question.strip(), f"{tool.name} does not say what it is for"


# ── argument handling ────────────────────────────────────────────────────────


def test_planner_names_are_translated_to_gsql_parameter_names():
    reg, _ = registry()
    wire = reg.get("region_novelty").build_params({"card_id": CARD, "region": REGION, "as_of": TS})
    # The planner says card_id; GSQL declares c_in.
    assert wire == {"c_in": CARD, "region": REGION, "as_of": TS}


def test_a_vertex_parameter_is_sent_bare_not_as_a_tuple():
    # v1 wrapped VERTEX<T> arguments in 1-tuples for pyTigerGraph. Over REST that
    # is wrong, and carrying the quirk forward would have been silent.
    reg, _ = registry()
    wire = reg.get("txn_detail").build_params({"txn_id": TXN})
    assert wire["t_in"] == TXN
    assert not isinstance(wire["t_in"], tuple)


def test_an_optional_argument_takes_its_declared_default():
    reg, _ = registry()
    wire = reg.get("amount_band_probe").build_params({"card_id": CARD, "amt": 49.0, "as_of": TS})
    assert wire["tol"] == 0.5


async def test_bad_arguments_produce_a_failed_result_not_an_exception():
    # A planner will eventually emit a malformed argument. The run must survive it
    # and the trace must show the question that went unanswered.
    reg, repo = registry()
    result = await reg.call("amount_band_probe", {"card_id": CARD, "amt": "not-a-number"})
    assert result.ok is False
    assert result.error
    assert reg.log.count == 1
    assert repo.call_count() == 0, "a malformed call must never reach the graph"


def test_an_unknown_tool_name_raises():
    # Distinct from a failed call: no honest ToolResult can cite a tool that does
    # not exist, and the planner validates names against names() first.
    reg, _ = registry()
    with pytest.raises(ValueError, match="unknown tool"):
        reg.get("does_not_exist")


# ── execution and logging ────────────────────────────────────────────────────


async def test_a_successful_call_is_parsed_logged_and_cited():
    repo = FakeGraphRepository().seed_query(
        "region_novelty",
        {
            "prior_txns_in_region": 42,
            "total_txns_in_region": 51,
            "prior_txns_on_card": 980,
            "first_seen_in_region": "2016-07-09 23:53:35",
            "last_in_region_before_alert": "2016-12-09 10:00:00",
            "in_person_elsewhere_24h": 10,
            "other_regions_24h": ["204.0", "264.0"],
        },
    )
    reg, _ = registry(repo)
    result = await reg.call("region_novelty", {"card_id": CARD, "region": REGION, "as_of": TS})

    assert result.ok is True
    assert result.data is not None
    assert result.data.prior_txns_in_region == 42  # the hand-verified number
    assert result.ref == f"query:region_novelty(card_id={CARD}, region={REGION}, as_of={TS})"
    assert CARD in result.entity_ids
    assert reg.log.count == 1
    assert reg.log.refs() == [result.ref]


async def test_a_graph_failure_is_recorded_rather_than_raised():
    reg, _ = registry()  # nothing seeded, so the fake raises QueryFailed
    result = await reg.call("card_baseline", {"card_id": CARD})
    assert result.ok is False
    assert "query_failed" in (result.error or "")
    assert reg.log.count == 1
    assert reg.log.failures() == [result]


async def test_each_investigation_gets_its_own_log():
    # v1's GraphTools made its own QueryLog by default, so twenty concurrent
    # cases would have merged into one trace. There is no default here.
    repo = FakeGraphRepository().seed_query(
        "card_baseline", {"card": [], "customer": [], "sibling_cards": []}
    )
    first = ToolRegistry(repo, QueryLog())
    second = ToolRegistry(repo, QueryLog())
    await first.call("card_baseline", {"card_id": CARD})
    assert first.log.count == 1
    assert second.log.count == 0


async def test_the_log_totals_feed_the_answer_file():
    repo = FakeGraphRepository().seed_query(
        "card_baseline", {"card": [], "customer": [], "sibling_cards": []}
    )
    reg, _ = registry(repo)
    await reg.call("card_baseline", {"card_id": CARD})
    await reg.call("card_baseline", {"card_id": CARD})
    # tool_calls and part of latency_s in the answer file come from here.
    assert reg.log.count == 2
    assert reg.log.total_seconds >= 0.0


# ── the facts that are easy to get wrong ─────────────────────────────────────


def test_ring_expand_requires_the_device_specificity_gate():
    # Ungated, 116 of 9,704 profiles carry 24,653 card links and the largest spans
    # 842 cards — R6 would then fire CREATE_CASE + FILE_REPORT on almost anything.
    reg, _ = registry()
    wire = reg.get("ring_expand").build_params(
        {"card_id": CARD, "center": TS, "days": 30, "max_device_cards": 20}
    )
    assert wire["max_device_cards"] == 20


async def test_card_baseline_separates_the_seed_card_from_its_siblings():
    # sibling_cards includes the seed card. Counting it as an "other card" would
    # inflate every BLOCK_ALL_CARDS arithmetic by one.
    repo = FakeGraphRepository().seed_query(
        "card_baseline",
        {
            "card": [{"v_id": CARD, "attributes": {"card_id": CARD, "n_txns": 1134}}],
            "customer": [{"v_id": "C08623", "attributes": {"customer_id": "C08623"}}],
            "sibling_cards": [
                {"attributes": {"SIB.card_id": CARD, "SIB.n_txns": 1134}},
                {"attributes": {"SIB.card_id": "C08623-K1", "SIB.n_txns": 210}},
            ],
        },
    )
    reg, _ = registry(repo)
    result = await reg.call("card_baseline", {"card_id": CARD})
    assert result.ok is True
    assert [card.card_id for card in result.data.other_cards] == ["C08623-K1"]
    assert result.data.n_other_cards == 1
    assert len(result.data.sibling_cards) == 2, "the raw list still carries the seed card"


# ── the simulator's three-way device branch ──────────────────────────────────


def test_a_step_up_invents_nothing_when_there_is_no_identity_record():
    """Seven of the twenty alerts carry no device at all.

    The regression this pins: `_step_up` read `prior_txns_this_device_on_card
    == 0` as "unrecognised device" and posted +1.1 log-odds for a failed
    challenge. On a case already near 0.84 that carried it past `stop_high` and
    flipped the verdict to fraud, on the strength of the only device claim in
    the evidence list — one the graph never supported.
    """
    from sentinel.domain.enums import CustomerResponse, RequestType
    from sentinel.simulation.simulator import EvidenceSimulator
    from sentinel.tools.dto import DeviceNovelty, TxnFlagsRow

    blind = DeviceNovelty(
        flags=TxnFlagsRow(txn_id="T1", device_key=""), prior_txns_this_device_on_card=0
    )
    assert blind.observable is False
    response = EvidenceSimulator(device=blind).simulate(RequestType.STEP_UP_AUTH)
    assert response.log_lr == 0.0
    assert response.branch is CustomerResponse.NO_REPLY
    assert "no identity record" in response.claim.lower()
    assert all(basis for basis in response.assumption_basis), "no empty citations"


def test_a_step_up_still_discriminates_when_the_device_is_observable():
    from sentinel.domain.enums import CustomerResponse, RequestType
    from sentinel.simulation.simulator import EvidenceSimulator
    from sentinel.tools.dto import DeviceNovelty, TxnFlagsRow

    known = DeviceNovelty(
        flags=TxnFlagsRow(txn_id="T1", device_key="d-1"), prior_txns_this_device_on_card=12
    )
    unknown = DeviceNovelty(
        flags=TxnFlagsRow(txn_id="T2", device_key="d-2"), prior_txns_this_device_on_card=0
    )
    passed = EvidenceSimulator(device=known).simulate(RequestType.STEP_UP_AUTH)
    failed = EvidenceSimulator(device=unknown).simulate(RequestType.STEP_UP_AUTH)
    assert passed.branch is CustomerResponse.STEP_UP_PASSED and passed.log_lr < 0
    assert failed.branch is CustomerResponse.STEP_UP_FAILED and failed.log_lr > 0


def test_a_cardholder_who_reported_the_charge_cannot_then_confirm_it():
    """The alert on eight of the twenty *is* the cardholder's denial.

    The regression this pins: HHG-009 came back with CREATE_CASE,
    WARN_CUSTOMER and CLOSE_NO_FRAUD together — open a case and close it as no
    fraud — because the simulator had the cardholder confirm a charge they had
    opened the case by disputing. Where the facts say the charge looks like
    theirs, the honest record is a contest between the facts and the
    cardholder, which is what R7 is for.
    """
    from sentinel.domain.enums import CustomerResponse, RequestType
    from sentinel.simulation.simulator import EvidenceSimulator
    from sentinel.tools.dto import RecurringChargeProbe

    recurring = RecurringChargeProbe(matching_charges=53, distinct_months=6)
    refs = {"recurring_charge_probe": "query:recurring_charge_probe(card_id=C1-K1)"}

    asked_cold = EvidenceSimulator(recurring=recurring, refs=refs).simulate(
        RequestType.CUSTOMER_VALIDATION
    )
    assert asked_cold.branch is CustomerResponse.CONFIRMED

    asked_after_a_report = EvidenceSimulator(
        recurring=recurring, refs=refs, already_denied=True
    ).simulate(RequestType.CUSTOMER_VALIDATION)
    assert asked_after_a_report.branch is CustomerResponse.NO_REPLY
    assert "contest" in asked_after_a_report.assumed_response.lower() or (
        asked_after_a_report.log_lr == 0.0
    )


def test_an_analyst_may_read_the_history_the_cardholder_disputes():
    """Policy 5's third option, and the one left when there is no device."""
    from sentinel.domain.enums import CustomerResponse, RequestType
    from sentinel.simulation.simulator import EvidenceSimulator
    from sentinel.tools.dto import RecurringChargeProbe

    response = EvidenceSimulator(
        recurring=RecurringChargeProbe(matching_charges=53, distinct_months=6),
        refs={"recurring_charge_probe": "query:recurring_charge_probe(card_id=C1-K1)"},
        already_denied=True,
    ).simulate(RequestType.ANALYST_INFO)
    assert response.branch is CustomerResponse.CONFIRMED
    assert "analyst" in response.claim.lower()
    assert "cardholder confirmed" not in response.claim.lower()
