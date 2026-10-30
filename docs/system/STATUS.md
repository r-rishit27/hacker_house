# Sentinel — Build Status

Where the build stands, and what is left. Every "done" below is backed by a
command whose output is quoted; nothing is marked complete on the strength of a
file existing.

**As of 2026-09-21.** Branch `sentinel-v2`.

Companion to [EXECUTION_PLAN.md](EXECUTION_PLAN.md), which has the full task
breakdown. This document is the score against it.

---

## The one-line answer

**The submission exists and the console runs on top of it.** Twenty answer
files, all valid against the contract *and* against the live graph, produced
by one command, with every case written back as a `FraudCase` vertex. GraphRAG
is live and its footprint is in the files. The API serves twenty-nine
endpoints and the console renders them. What remains is the demo video.

```
$ python -m sentinel run --all
20/20 valid · block 10% · SAR 10% · changed 40% · 142,128 tokens · 390s
verdicts: {'uncertain': 9, 'fraud': 8, 'legitimate': 3}
```

No warnings. Block rate is a fifth of its 0.50 ceiling, SAR rate half of its
0.20 ceiling, no verdict covers more than 45% of the pack, and eight of the
twenty changed their recommendation after the evidence round, against a target
of six.

---

## Verified state

```
pytest tests/unit tests/integration -q      349 passed
pytest tests/live -m live -q                16 passed   (against the live workspace)
ruff check api sentinel tests               All checks passed!
ruff format --check api sentinel tests      121 files already formatted
mypy --strict                               Success: no issues found in 101 source files
python -m sentinel validate --no-graph      20/20 valid
python -m api.contract emit --check         up to date
cd frontend && npx tsc --noEmit             clean
cd frontend && npm run build                Compiled successfully
cd frontend && npm run smoke                28/28 checks passed  (headless Chromium)
python scripts/check_contrast.py --check    every token clears its bar in both themes
```

The unit and integration suites need no network and no credentials: the agent
runs against `FakeGraphRepository` and a stub model, and the API's fourteen
tests run the real container, the real permission policy and the real journal
over an in-memory database.

---

## Milestones

| # | Milestone | Exit test | State |
|---|---|---|---|
| **M0** | Environment real | install, suite, lint, types | ✅ |
| **M1** | Domain ported | 40 v1 policy tests from the new home; live tool assertions | ✅ |
| **M2** | Tools agent-ready | ≥16 tool schemas; live sweep reproduces the hand investigation | ✅ 19 tools |
| **G1** | New GSQL queries | 10 new queries installed and callable | ✅ 26 live, all with source |
| **M3** | GraphRAG live | `PolicyDoc` > 0; 5,565 `ClosedCase.emb`; retrieval recall ≥ 4/6 | ✅ **5 of 6** |
| **M4** | One case end to end | valid file, non-zero tokens, ≥1 `document` evidence item | ✅ |
| **M5** | **20 answer files** ← the gate | all valid; block ≤ 0.50; ≥6 with initial ≠ final | ✅ **20/20, 8 changed** |
| **M6** | Console usable | live stream, reconnect replays | ✅ **verified over HTTP, and in a browser** |
| **M7** | Permission boundary | 403 → approve → execute | ✅ **end to end, and tested** |
| **M8** | Score raised | calibration review, ring discovery | ✅ both |
| **M9** | Shipped | video, blog, social | ⚠️ blog and social drafted; **no video** |

---

## Done, in detail

### The graph and its tools

| | |
|---|---|
| `GRAPH_GOA` on Savanna 4.2.5 | 590,742 transactions · 14,317 cards · 5,565 closed cases · 9,704 device profiles · 20 alerts · 46 policy chunks · 20 Sentinel cases |
| GSQL queries installed | **26**, every one with source in `graph/queries/`. Two `_zz_` orphans with no source were dropped |
| Tools exposed to the agent | **19** (the console's read queries are deliberately withheld) |
| TigerGraph MCP | `tigergraph-mcp` 1.0.3 over stdio, **69 tools**, verified returning the hand-checked 42 prior transactions in region 330.0 |

### GraphRAG

| | |
|---|---|
| `PolicyDoc` | **46 chunks**, parsed out of Guide.md rather than transcribed — 10 rules, 9 policy sections, 5 patterns, 16 glossary terms, 6 guidance notes |
| `ClosedCase.emb` | **5,565 of 5,565**, `text-embedding-3-large` at 256 dimensions, about $0.01 |
| Retrieval | Cosine over 5,611 vectors fused with a structural ranking by RRF, hard-filtered on `opened_at < as_of` |
| Recall | **5 of HHG-003's 6 hand-found closed cases** in the top 10, against a target of 4 |
| Visible footprint | `source: "document"` evidence on every case, each citing a `PolicyDoc` the graph identity checker proves exists |

### The backend

44 modules under `backend/sentinel/`, plus `backend/api/`.

| Package | What it does |
|---|---|
| `graph/` | Async repository over REST++, cold-start detection, token re-mint, batched upsert, `FakeGraphRepository`, and the normaliser |
| `tools/` | 19 typed tools, one `QueryLog` per investigation, `EvidenceRef` as the single place a citation is built |
| `evidence/` | The fitted table, the log-odds ledger, `FeatureExtractor` over 29 features |
| `policy/` | `PolicyEngine`, `RoutingTable`, `SarPolicy`, `StoppingPolicy`, six gate classes |
| `rag/` | `CorpusBuilder`, `EmbeddingService`, `VectorIndex`, `GraphRagRetriever` |
| `memory/` | `CaseMemoryStore` — one vertex, eight edge types, one POST |
| `agents/` | The ten-step orchestrator, four LLM agents, episode scoper, state builder, assembler |
| `exploration/` | Ring discovery; output to `exploration/`, never to `cases/` |
| `runner.py`, `cli.py` | The run harness and the composition root |

### Twenty-five defects found and fixed

Four were in the v1 base; the rest were found by running the machine,
auditing its twenty answer files, and pointing a browser at the API. Each has
a regression test.

**In the base**

1. **The ledger's group cap** clamped the increment by remaining room, so
   exonerating evidence arriving after incriminating evidence in the same group
   was over-suppressed — damaging exactly the legitimate half of the benchmark.
2. **`independent_signals` was never wired** to `ledger.independent_support()`,
   so R1 stripped `BLOCK_CARD` from every case under p = 0.70.
3. **`elt.json` had no `analyst_request` prior**, so HHG-014 ran at 0.5 silently.
4. **The validator conflated a failed lookup with a missing vertex.**

**Found by running it**

5. **The planner dropped the exonerating detectors.** Free to choose it picked
   6 of 11 and the five it dropped were the amount band, the amount
   distribution and the recurring probe: p = 0.74 instead of 0.36 on identical
   facts.
6. **The planner's schema was inexpressible.** A free-form `args` dict cannot
   satisfy OpenAI strict mode.
7. **Truncation was retried as a schema violation**, four times, burning 19k
   tokens. Now detected by `finish_reason`.
8. **`R2` could not fire on the eight cases where the cardholder denied
   outright.** `TriggerContext.customer_already_denied` was computed and read by
   nothing.
9. **`import sentinel` from the repository root resolved to the superseded v1
   package**, so `python -m sentinel` ran the wrong code.

**Found by auditing the output**

10. **One `CostMeter` served the whole batch.** `answer.tokens` climbed from
    7,541 on the first case to 122,847 on the twentieth — the batch total — and
    the per-run token ceiling tripped two thirds of the way through.
11. **`initial` was evaluated at the post-request probability.** HHG-006's
    requested step-up moved the number 0.8111 → 0.5883 and the *pre*-request
    recommendation was computed at 0.5883, so the two phases could not disagree.
12. **The agent was nondeterministic.** Four identical runs returned 0.3569,
    0.3577 and 0.5025. See [CALIBRATION.md](../CALIBRATION.md).
13. **The risk score was counted twice** on the alerts the score raised.
14. **Four evidence groups held one observation** — being online — contributing
    an identical +1.84 log-odds to every fraud verdict.
15. **The simulator invented a failed step-up** on the seven alerts with no
    identity record, worth +1.1 log-odds each.
16. **The simulator let a cardholder confirm a charge they had just reported**,
    producing `CREATE_CASE` and `CLOSE_NO_FRAUD` in the same file.
17. **One abbreviation cost a whole answer file.** `count_sentences` read "U.S.
    dollars" as two sentences and the resulting `ValidationError` escaped the
    orchestrator's except clauses.
18. **A narrower group cap inverted a posting's sign**: +0.4 applied as −0.5.
19. **`scripts/setup_mcp.py --refresh` deleted a live `OPENAI_API_KEY`.**
20. **A named pattern could contradict a measured fact** — `out_of_region_use`
    on a card with 42 prior transactions in that region.

**Found by pointing a browser at it**

21. **`Subscription` was unhashable**, so the SSE broker's set raised
    `TypeError` from inside the response body — after the 200 had gone out,
    which is why it read as an empty stream rather than an error.
22. **The wire said `on_denied: "enqueue"` and the service checked
    `"approve"`**, so every 403 came back without the approval it had created.
23. **The run row said "completed" before its events were journalled**, and
    `_finish` read the step number and budget back out of a half-drained
    journal.
24. **`sqlalchemy` was declared without `[asyncio]`**, so a fresh install
    failed on the first await with "the greenlet library is required".
25. **`pytest -m live` skipped all sixteen tests silently.** They read
    `os.environ` for credentials that live in `.env`, and "16 skipped" reads,
    at a glance, exactly like "16 passed".

### The model

`gpt-5.4-mini`, `text-embedding-3-large` at 256 dimensions. Both verified live.

**The finding that shaped the prompts:** asked cold with the HHG-003 facts, the
model named the pattern `card_testing` — twice — and its own explanation gave
it away: *"the same $49.00 amount has repeated many times."* That is a
recurring charge, which R7 says must never be blocked. The pattern definitions
are now carried verbatim in the prompt, and the policy path has no model in it.

---

## Remaining

| id | Work | Why it matters |
|---|---|---|
| **Z5** | Demo video, 3–5 minutes | **Required, and cannot be produced from a terminal** — it needs a screen recording. The console's own **Run demo** button now walks the three acts unattended, so the recording is a screen capture rather than a performance |
| **Z6/Z7** | Publish the blog and the social post | Both drafted — [BLOG.md](../BLOG.md), [SOCIAL.md](../SOCIAL.md). Two URLs to fill in once they are live |

**F10 is closed.** The graph canvas is built and draws the live subgraph; see
below.

### The API and the console

Thirty endpoints under `/api`. **One screen**, not four: the console is a
single surface — queue rail, case panel, graph pane — with three views
(console, monitor, ring) and the approvals inbox as a drawer over the top,
ported from the design study in `UI/`. The permission walk is verified end to
end, over HTTP and in the test suite:

```
analyst POSTs BLOCK_CARD   -> 403 forbidden_route, naming team_lead and
                               fraud_manager, carrying the approval it enqueued
same analyst approves it   -> 403 role_insufficient_for_approval
team_lead approves it      -> executed, simulated, audited
```

SSE carries `retry: 2000` and a monotonic `id:` per run; reconnecting with
`Last-Event-ID: 30` returns 31 onward, contiguous, with no duplicate.

**The run flow.** Nineteen of the twenty cases were investigated days ago, so
their evidence is on disk rather than on the wire. The console replays it at
the cadence it was produced — one posting every 460ms, the probability moving
as each lands — and labels it `replay of <run id>`, because a replay dressed
as a live run is a lie about when the work happened. **Run demo** walks three
acts over three real cases chosen from the live queue by what the agent
concluded: the strongest fraud, the case the bank's model scored highest and
the agent cleared anyway, and the ring that is not there.

**The graph canvas** (F10, previously cut) draws `/api/graph/cases/{id}` as a
breadth-first ring layout — deterministic, so the same case draws the same
picture on two screens in a call. **The ring view** has to carry two facts at once. At one hop — the hop R6 is
defined on — no two of the twenty seeds share a device profile, so the
headline is the negative finding and stays that way. But widening to two hops
with the same specificity gate, one fingerprint (`Deff4ac1c3cb9`) carries
**twelve cards belonging to twelve different customers, seven of which the
bank has already closed as confirmed fraud, with $73,178 of exposure on those
closed cases**. That satisfies the same component test the ring gate applies
and is not a ring, because it is not at one hop — the panel says exactly that.
The canvas draws it the way the design study draws a ring: the shared profile
at the centre, its cards around it, each named, the confirmed ones marked.
Every number comes from `exploration/rings.json`, so the page and
`explore rings` cannot disagree.

Verified in a real browser: `npm run smoke` drives Chromium through the
masthead, the queue, a replay advancing, the graph pane's nodes, all five
tabs, both full-screen views and the drawer — 28 checks, failing on any
console error. It runs with the OS forced to dark, because the console is a
light instrument and following the preference was a defect it caught.

### Ring discovery

`python -m sentinel explore rings` writes `exploration/rings.{json,md}`. The
result is a negative one and it is the honest one: **no ring among the twenty
at one hop**, and two hops reaches a single component of 1,374 cards across
1,354 customers — the giant-component failure the specificity gate exists to
prevent, one hop further out. R6 is right not to fire on this pack.

### What cannot be finished from a terminal

The **demo video** is a required submission artefact and needs someone to
record a screen. Everything it should show now runs; the runbook in
[EXECUTION_PLAN.md §10](EXECUTION_PLAN.md#10-demo-day-runbook) lists the five
beats in order, and all five are reachable in the console.

---

## Next three things

1. **Record the video.** Start the API and the console, then walk the runbook.
2. **Publish the blog** and the social post, and fill the two URLs in.
3. **The graph canvas**, if there is time. It is the one cut-list item still
   cut.
