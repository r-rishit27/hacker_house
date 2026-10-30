"""The normalizer is the boundary where TigerGraph's shapes stop leaking.

Four things go wrong without it, and all four were observed against the live
workspace rather than imagined:

  * results arrive as a list of single-key blocks, one per ``PRINT ... AS``;
  * 12 of the 16 queries prefix attribute keys with the GSQL alias, 4 do not;
  * an empty ``MaxAccum<DOUBLE>`` prints ``-1.797693134862316e+308``;
  * an empty DATETIME accumulator prints ``"1970-01-01 00:00:00"``.

The last two are the dangerous ones. Left alone they reach the model, which will
faithfully report that a card was first seen in a region in 1970.
"""

from __future__ import annotations

from sentinel.graph.normalize import ResponseNormalizer, is_cold_start

NEG_INF = -1.797693134862316e308
EPOCH = "1970-01-01 00:00:00"


def normalizer() -> ResponseNormalizer:
    return ResponseNormalizer()


# ── flattening ───────────────────────────────────────────────────────────────


def test_flatten_merges_the_single_key_blocks_tigergraph_returns():
    # Real shape: region_novelty prints seven separate accumulators.
    blocks = [
        {"prior_txns_in_region": 42},
        {"total_txns_in_region": 51},
        {"prior_txns_on_card": 980},
    ]
    assert normalizer().flatten(blocks) == {
        "prior_txns_in_region": 42,
        "total_txns_in_region": 51,
        "prior_txns_on_card": 980,
    }


def test_flatten_tolerates_an_empty_or_absent_result():
    assert normalizer().flatten([]) == {}
    assert normalizer().flatten(None) == {}


# ── alias stripping ──────────────────────────────────────────────────────────


def test_strip_alias_removes_the_gsql_projection_prefix():
    # card_window prints T[T.txn_id, T.ts, ...]; the alias is an artefact of GSQL,
    # not part of the contract with the agent.
    row = {"T.txn_id": "3530164", "T.amt": 49.0, "T.channel": "in_person"}
    assert normalizer().strip_alias(row) == {
        "txn_id": "3530164",
        "amt": 49.0,
        "channel": "in_person",
    }


def test_strip_alias_leaves_bare_keys_alone():
    # txn_detail prints the vertex unprojected, so its keys arrive bare.
    row = {"txn_id": "3530164", "amt": 49.0}
    assert normalizer().strip_alias(row) == row


def test_strip_alias_keeps_accumulator_names_readable():
    # ring_expand projects C2.@shared; the '@' is GSQL's accumulator marker.
    row = {"C2.card_id": "C08623-K1", "C2.@shared": 3}
    out = normalizer().strip_alias(row)
    assert out["card_id"] == "C08623-K1"
    assert out[next(k for k in out if "shared" in k)] == 3


# ── the sentinels ────────────────────────────────────────────────────────────


def test_empty_max_accum_becomes_none():
    # card_testing_probe.largest_purchase_after when no purchase cleared.
    assert normalizer().sanitize(NEG_INF) is None


def test_empty_datetime_accum_becomes_none():
    # region_novelty.first_seen_in_region when the card has never used the region.
    assert normalizer().sanitize(EPOCH) is None


def test_sentinels_are_removed_from_nested_structures():
    raw = {
        "largest_purchase_after": NEG_INF,
        "first_seen_in_region": EPOCH,
        "window": [
            {"T.txn_id": "1", "T.max_risk": NEG_INF},
            {"T.txn_id": "2", "T.max_risk": 0.9},
        ],
    }
    out = normalizer().sanitize(raw)
    assert out["largest_purchase_after"] is None
    assert out["first_seen_in_region"] is None
    assert out["window"][0]["T.max_risk"] is None
    assert out["window"][1]["T.max_risk"] == 0.9


def test_a_real_zero_survives_sanitisation():
    # The whole point of the sentinel check: 0 and 0.0 are legitimate answers.
    # card_testing_probe.small_online_auths_1h == 0 means "none", not "unknown".
    assert normalizer().sanitize(0) == 0
    assert normalizer().sanitize(0.0) == 0.0
    assert normalizer().sanitize("") == ""


def test_a_real_date_survives_sanitisation():
    assert normalizer().sanitize("2016-12-10 13:01:21") == "2016-12-10 13:01:21"


def test_a_large_but_real_negative_number_survives():
    # Only the exact float64 minimum is the sentinel. A big loss is not one.
    assert normalizer().sanitize(-1_000_000.0) == -1_000_000.0


# ── the whole pipeline ───────────────────────────────────────────────────────


def test_normalize_flattens_and_sanitises_in_one_pass():
    blocks = [
        {"small_online_auths_1h": 0},
        {"largest_purchase_after": NEG_INF},
        {"small_txn_ids": []},
    ]
    out = normalizer().normalize(blocks)
    assert out["small_online_auths_1h"] == 0
    assert out["largest_purchase_after"] is None
    assert out["small_txn_ids"] == []


def test_rows_unwraps_tigergraph_vertex_envelopes():
    # Printed vertices arrive as {v_id, v_type, attributes}; callers want the attrs.
    value = [
        {"v_id": "C08623-K2", "v_type": "Card", "attributes": {"C.median_amt": 68.01}},
        {"v_id": "C08623-K1", "v_type": "Card", "attributes": {"C.median_amt": 31.5}},
    ]
    rows = normalizer().rows(value)
    assert len(rows) == 2
    assert rows[0]["median_amt"] == 68.01


# ── cold start ───────────────────────────────────────────────────────────────


def test_the_cold_start_page_is_recognised():
    # The workspace auto-stops. The first call back returns HTML, not JSON, and
    # it is ready ~45 s later. Reporting this as an error would be a lie.
    body = "<html>\n<title>Starting workspace</title>\n<body><h1>Starting workspace</h1>"
    assert is_cold_start(body) is True


def test_real_json_is_not_mistaken_for_a_cold_start():
    assert is_cold_start('{"error":false,"results":[{"prior_txns_in_region":42}]}') is False


def test_an_html_page_that_is_not_the_cold_start_is_not_one():
    assert is_cold_start("<html><title>404 Not Found</title></html>") is False
