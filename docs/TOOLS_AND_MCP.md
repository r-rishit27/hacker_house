# The tool layer: 26 GSQL queries, exposed over MCP

The agent's entire view of the graph is a fixed set of questions. Sixteen were
written for the hand investigation; ten more were added for the console, the
corpus and the two-hop ring. Nineteen of the twenty-six are exposed to the
agent — the console's own read queries are deliberately withheld, because a
tool the agent can call is a tool it can call on the wrong case.

```bash
python scripts/install_queries.py     # create + install (compiles, ~4 min)
python scripts/setup_mcp.py           # mint token, write .env + .mcp.json, verify
python scripts/setup_mcp.py --check   # start the MCP server and call a query through it
python scripts/setup_mcp.py --refresh # tokens expire; re-mint
```

Every installed query has its source in `graph/queries/`. That is checked
rather than assumed: two `_zz_` scratch queries were once installed with no
source, making the live count 28 where the repo produced 26, so a graph rebuilt
from the repo silently differed from the one every number was measured
against. They are dropped.

The 34 live assertions that used to live in `scripts/test_tools.py` are now
`backend/tests/live/test_graph_repository.py`, run with `pytest -m live`.

## The first sixteen

| # | Query | Answers |
|---|---|---|
| 1 | `txn_detail` | The flagged transaction, its device and its card |
| 2 | `card_baseline` | The cardholder's normal, plus their other cards |
| 3 | `card_window` | Ordered neighbours within ±N hours |
| 4 | `card_testing_probe` | Small online auths in an hour, and what cleared after (R5) |
| 5 | `region_novelty` | Is this billing region new **as of the alert**, and was the card in person elsewhere that day |
| 6 | `amount_band_probe` | How many prior charges sit in the same dollar band |
| 7 | `device_novelty` | `id_15`/`id_23`/`id_34`, and whether the card ever used this device before |
| 8 | `device_neighbors` | Other cards on the same profile, **and how many cards that profile spans** |
| 9 | `region_cluster` | High-risk share in a window **against a matched baseline window** (R6) |
| 10 | `email_cluster` | Recipient-email fan-in across cards (R6) |
| 11 | `ring_expand` | Cards linked by a *specific* shared device |
| 12 | `recurring_charge_probe` | Same amount and product over months (R7) |
| 13 | `velocity_probe` | Count, spend, distinct regions and devices in a window |
| 14 | `customer_case_history` | This customer's closed cases **and their denial track record** |
| 15 | `similar_prior_cases` | Structural retrieval over the 5,565 closed cases |
| 16 | `case_memory_for_card` | Bank closed cases **and cases Sentinel wrote earlier in the run** |

## The ten added since

| # | Query | Answers | Exposed to the agent |
|---|---|---|---|
| 17 | `txn_sequence_context` | The `NEXT` edge either side — seconds since the previous transaction on this card | yes |
| 18 | `card_amount_stats` | Raw sums, so the caller can see the sample size behind a mean | yes |
| 19 | `product_novelty` | Has this card bought this product code before | yes |
| 20 | `device_by_label` | A device profile by its composite label, replacing an interpolated full scan | yes |
| 21 | `ring_expand_2hop` | Two hops through *specific* profiles, with the card-device edge list | yes |
| 22 | `alerts_queue` | All 20 alerts joined to any case — the console's work queue | no |
| 23 | `case_by_id` | One `FraudCase` and its six edge sets | no |
| 24 | `case_subgraph` | Nodes and edges shaped for the graph canvas | no |
| 25 | `policy_docs_all` | The `PolicyDoc` corpus, for the vector index | no |
| 26 | `closed_case_range` | Closed cases by id range, with or without `emb` | no |

`txn_sequence_context` is the one worth naming: it reads the **576,425 `NEXT`
edges** that no query in the first sixteen ever traversed. On HHG-003's flagged
transaction it returns `seconds_since_prev = 2920` — 48.7 minutes, inside the
one-hour burst band, and a fitted feature that had no tool behind it until it
existed.

Two conventions worth knowing:

- **Vertex-typed parameters** (`VERTEX<Card> c_in`) seed traversal in O(1). String
  parameters mean a scan, so they are used only on the small vertex types
  (ClosedCase 5,565; BillingRegion 332). Passing them through pyTigerGraph
  requires 1-tuples — `{"c_in": ("C08623-K2",)}` — or it silently falls back to a
  deprecated GET path.
- **Queries return facts, not verdicts.** R5's "three or more", R1's 0.70 and the
  rest live in `backend/sentinel/policy/engine.py` where they are unit-testable. A query may
  compute a convenience count; nothing is allowed to treat it as the decision.

## What the exit test caught

`scripts/test_tools.py` re-asserts every number from the hand investigation
against the installed queries. Two disagreements surfaced, and only one was a
test bug.

**The test was wrong about the window.** `card_window(±72h)` returns 43, not the
62 of the hand investigation — that used a 7-day calendar span. Verified against
staging: 43 is correct.

**The tool was wrong about rings.** `ring_expand` reported 25 "connected" cards
for a case whose flagged transaction is in person and has no device record at
all. The cause is that a `DeviceProfile` is `DeviceInfo | OS | browser | screen`
— a fingerprint *class*, not a device identity:

| Cards per profile | Profiles | Card-links |
|---|---|---|
| 1 | 4,913 | 4,913 |
| 2–5 | 3,040 | 8,723 |
| 6–20 | 1,227 | 12,256 |
| 21–100 | 408 | 16,455 |
| **>100** | **116** | **24,653** |

116 profiles — 1.2% of them — carry 24,653 of the card-to-device links.
`Windows | Windows 10 | chrome 63.0 | 1920x1080` spans 842 cards. Linking cards
through those invents a ring on almost any card ever used online, which under R6
would trigger `CREATE_CASE`, `FILE_REPORT` and `MONITOR_CONNECTED_CARDS` — a
false SAR.

`ring_expand` now takes `max_device_cards` (default 20, covering 94.6% of
profiles) and reports `devices_total` alongside `devices_specific_enough`, so
evidence can state the specificity rather than implying a link exists.

Even then, treat it as graded rather than binary: a 4-card iPad profile is
suggestive, not proof of a shared physical device. The probability ledger should
weight a device link by profile rarity, not count it as a fact.

## MCP

`tigergraph-mcp` 1.0.3, stdio transport, configured by `.mcp.json`:

```json
{
  "mcpServers": {
    "tigergraph": {
      "command": "tigergraph-mcp",
      "args": [],
      "env": { "TG_HOST": "...", "TG_GRAPHNAME": "GRAPH_GOA",
               "TG_RESTPP_PORT": "443", "TG_GS_PORT": "443" }
    }
  }
}
```

Credentials stay in `.env`, which the server loads from the working directory and
which is gitignored. It accepts `TG_SECRET` or `TG_API_TOKEN`; both were tested
and both work. `scripts/setup_mcp.py` mints the token from the Savanna secret and
keeps the MCP-named variables (`TG_GRAPHNAME`, `TG_GS_PORT`, `TG_API_TOKEN`) in
sync with Sentinel's own (`TG_GRAPH`, `TG_GSQL_PORT`, `TG_SECRET`) inside a single
managed block.

The server exposes 69 tools; the ones that matter here are
`tigergraph__run_installed_query`, `run_query`, `install_query` and `show_query`.
`setup_mcp.py --check` verifies the handshake, the tool list, and then runs
`region_novelty` through MCP and asserts it returns the hand-verified 42.

**One trap worth recording.** Driving the stdio server by writing all requests and
closing stdin makes it shut down on EOF and kill any call still in flight —
`tools/list` wins that race, a real query does not, and the only symptom is
`{"code": -32000, "message": "Connection closed"}` with empty stderr. The
check now holds stdin open until the replies arrive. A normal MCP client does
this anyway; it only bites test harnesses.

## Ports

Savanna terminates everything on 443, so `TG_RESTPP_PORT` and `TG_GS_PORT` are
both `443`, not the 9000/14240 defaults a local install uses.
