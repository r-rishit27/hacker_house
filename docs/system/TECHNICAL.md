# Sentinel v2 — Technical Document

The engineering handbook: how to build, run, operate and debug the system. Literal commands, real
paths, real variable names. Product intent is in [PRD.md](PRD.md), architecture in [HLD.md](HLD.md),
class detail in [LLD.md](LLD.md).

---

## Contents

1. [Technology stack](#1-technology-stack)
2. [Repository layout and migration](#2-repository-layout-and-migration)
3. [Configuration](#3-configuration)
4. [Local setup](#4-local-setup)
5. [Data and graph operations](#5-data-and-graph-operations)
6. [API reference](#6-api-reference)
7. [SSE event reference](#7-sse-event-reference)
8. [Tool reference](#8-tool-reference)
9. [LLM usage policy](#9-llm-usage-policy)
10. [Coding standards](#10-coding-standards)
11. [Testing and CI](#11-testing-and-ci)
12. [Observability and debugging](#12-observability-and-debugging)
13. [Operational runbook](#13-operational-runbook)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Technology stack

| Layer | Choice | Version | Why this |
|---|---|---|---|
| Language (backend) | Python | 3.11+ | `X \| Y` unions and `ExceptionGroup`; the existing code already uses `from __future__ import annotations` |
| Models | OpenAI Python SDK | ≥ 1.40 | Decision D1. Structured output with a JSON schema, so prose and data never mix |
| Data models | Pydantic | v2 | Validation at every boundary; the answer contract as a type |
| Settings | pydantic-settings | ≥ 2.0 | Typed, fail-fast env loading; replaces module-level globals and import-time `load_dotenv()` |
| HTTP server | FastAPI + uvicorn | ≥ 0.110 | Async, OpenAPI for free, native SSE via `StreamingResponse` |
| Graph client (runtime) | `httpx.AsyncClient` over REST++ | ≥ 0.27 | ~15 graph calls per case at 0.7–1.1 s each; async overlaps the independent ones (ADR-9) |
| Graph client (scripts) | pyTigerGraph | ≥ 1.6 | Already used by the working loader and installer; not on the hot path |
| Operational store | SQLAlchemy 2.0 async + aiosqlite | — | Runs, event journal, approvals, audit survive a restart |
| Numerics | numpy | ≥ 1.26 | The cached embedding matrix |
| ETL (offline) | DuckDB + pandas | — | Already used by `prepare_data.py` and `fit_elt.py` |
| Tests | pytest + pytest-asyncio | — | The 40 policy tests are already pytest |
| Lint / format | ruff | ≥ 0.5 | One tool for both |
| Types | mypy | strict on `backend/sentinel` | The decision path must be typed |
| Frontend | Next.js | 15 (App Router) | Decision D3 |
| Language (frontend) | TypeScript | 5.x, `strict: true` | |
| Styling | Tailwind CSS | 3.4 | Tokens as CSS custom properties consumed by `tailwind.config.ts`. v3 rather than v4 because the config is JS-first, which is what the design system's token mapping assumes |
| Primitives | Radix UI | latest | Behaviour and a11y; the visuals are ours |
| Variants | class-variance-authority + tailwind-merge | — | Typed component variants |
| Server state | TanStack Query | v5 | Caching, retries, invalidation |
| Client state | Zustand | v4 | The timeline store the SSE stream feeds |
| Charts | Visx or hand-rolled SVG | — | The trajectory and sparklines are small and bespoke; a chart library is not worth the bundle |
| Graph canvas | React Flow | v12 | The case subgraph |

**Not used, deliberately:** LangGraph, the OpenAI Agents SDK, LangChain (D1); MUI/Mantine (D3);
Postgres (SQLite is enough and has no infra cost); a vector database (the vectors belong in
TigerGraph — ADR-3).

## 2. Repository layout and migration

### 2.1 Target layout

```
hacker_house/
  backend/            pyproject.toml · sentinel/ (domain) · api/ (HTTP) · tests/ · var/
  frontend/           Next.js app + the design system
  graph/              schema.gsql · loading_jobs.gsql · queries/*.gsql      (unchanged)
  scripts/            prepare_data · load_to_tigergraph · install_queries · setup_mcp
  data/staging/       the 13 loader-ready files                              (unchanged)
  cases/              HHG-001.json … HHG-020.json — THE DELIVERABLE
  runs/               HHG-0NN.trace.json — derived, gitignored
  exploration/        autonomous-monitoring output (FR-39)
  eval/               fit_elt.py (offline fit, stays) · validate.py → thin CLI over the validator
  docs/               v1 write-ups · system/ (this set)
  Guide.md            the organiser's brief — the authority
```

### 2.2 Migration table

| Today | Becomes | What changes |
|---|---|---|
| `sentinel/config.py` | `backend/sentinel/config/settings.py` → `Settings` | Typed and validated; no import-time `load_dotenv`; `OPENAI_*` added |
| `sentinel/config.connect()` | `graph/TigerGraphConnectionFactory` + `TokenManager` | Token cached and re-minted on 401 instead of re-minted on every call |
| `sentinel/tools.py` `GraphTools` | `graph/GraphRepository` + `tools/ToolRegistry` | Split: transport vs the agent's typed tool surface |
| `sentinel/tools.py` `QueryLog` | `tools/QueryLog` | **One per investigation.** Today it defaults to a fresh instance inside `GraphTools.__init__`, so 20 concurrent cases would share one trace |
| `sentinel/tools.py` `_fmt_ref` | `tools/EvidenceRef` | Every `ref` string in the system built in one place |
| `sentinel/ledger.py` | `evidence/EvidenceLedger` + `EvidenceLikelihoodTable` | Table injected from `Settings.elt_path`; group cap fixed (LLD §4.2) |
| `sentinel/elt.json` | `backend/sentinel/evidence/elt.json` | **Moved with the module.** `ledger.py` locates it by `Path(__file__).with_name("elt.json")`, so moving one without the other raises at import |
| `sentinel/policy.py` | `policy/` package | `decide` → `PolicyEngine.decide`; `_enforce_gates` → six `PolicyGate` classes; thresholds → `PolicyConfig` |
| `sentinel/memory.py` | `memory/CaseMemoryStore` | `_customer_id`/`_card_id` smuggling and the interpolated GSQL scan deleted |
| `eval/validate.py` | `validation/AnswerValidator` + `GraphIdentityChecker` + a thin CLI | Duplicated enum tables deleted; "not found" distinguished from "lookup failed" |
| `eval/fit_elt.py` | unchanged, stays an offline script | Only its output is a runtime input |
| `scripts/*.py` | unchanged | Already run; the graph is live |
| `scripts/test_tools.py` | `backend/tests/live/test_graph_repository.py` | 34 assertions become pytest, `-m live` |
| `tests/test_policy.py` | `backend/tests/unit/test_policy.py` | **40 tests, import line only** |

### 2.3 Things being deleted

1. `sys.path.insert(0, ...)` in six files — replaced by `pip install -e backend/`.
2. `print()` as a reporting channel in library code; `sys.exit` inside library functions.
3. `except Exception:  # noqa: BLE001` in `validate.check_graph` (twice) — a transient auth failure currently reports as "does not exist in the graph".
4. `memory._device_key_for_label`'s interpolated `runInterpretedQuery` full scan.
5. The hand-maintained `TOOL_NAMES` list parallel to the 16 methods.
6. The duplicated enum and routing tables in `validate.py`.
7. `scripts/setup_mcp.py`'s `_rpc` / `HANDSHAKE` / `check` machinery — only `mint_token()` survives.

## 3. Configuration

### 3.1 Every variable

| Variable | Read by | Required | Default | Note |
|---|---|---|---|---|
| `TG_HOST` | `Settings.tg_host` | yes | — | Savanna workspace URL, no trailing slash |
| `TG_SECRET` | `Settings.tg_secret` | yes | — | Mints tokens; does not expire |
| `TG_GRAPH` | `Settings.tg_graph` | no | `GRAPH_GOA` | |
| `TG_RESTPP_PORT` | `Settings.tg_restpp_port` | no | `443` | Savanna terminates everything on 443 |
| `TG_GSQL_PORT` | `Settings.tg_gsql_port` | no | `443` | |
| `TG_API_TOKEN` | `Settings.tg_api_token` | no | — | Bearer token; **expires 2026-09-27**. Managed by `scripts/setup_mcp.py` |
| `TG_GRAPHNAME` | `tigergraph-mcp` | no | — | MCP reads its own names; kept in the managed block |
| `TG_GS_PORT` | `tigergraph-mcp` | no | — | |
| `TG_CHUNK_ROWS` | loader | no | `120000` | |
| `TG_MAX_CONCURRENCY` | `Settings.tg_max_concurrency` | no | `8` | Semaphore over graph calls |
| `TG_CONNECT_RETRIES` | `Settings.tg_connect_retries` | no | `3` | Cold-workspace tolerance |
| **`OPENAI_API_KEY`** | `Settings.openai_api_key` | **yes** | — | **New.** Backend only |
| **`OPENAI_MODEL`** | `Settings.openai_model` | **yes** | **none** | No default on purpose: a hard-coded model id is how an hour goes to a 404 |
| **`OPENAI_EMBEDDING_MODEL`** | `Settings.openai_embedding_model` | **yes** | **none** | Same reason |
| `OPENAI_EMBEDDING_DIMENSIONS` | `Settings.openai_embedding_dimensions` | no | `256` | ADR-4 |
| `MAX_TOOL_CALLS_PER_RUN` | `BudgetGuard` | no | `25` | |
| `MAX_EVIDENCE_ROUNDS` | `BudgetGuard` | no | `1` | The format has exactly `initial` and `final` |
| `MAX_TOKENS_PER_RUN` | `BudgetGuard` | no | `120000` | |
| `MAX_USD_PER_RUN` | `BudgetGuard` | no | `1.50` | |
| `MAX_RUN_SECONDS` | `BudgetGuard` | no | `300` | |
| `API_HOST` / `API_PORT` | uvicorn | no | `127.0.0.1` / `8000` | |
| `CORS_ORIGINS` | FastAPI | no | `http://localhost:3000` | |
| `DEFAULT_ROLE` | `PrincipalProvider` | no | `analyst` | |
| `NEXT_PUBLIC_API_BASE_URL` | frontend | yes | `http://localhost:8000` | **The only variable the frontend sees** |
| `NEXT_PUBLIC_FIXTURE_MODE` | frontend | no | `0` | `1` renders from bundled fixtures with no backend |

### 3.2 Secret hygiene

`OPENAI_API_KEY` and `TG_SECRET` never leave the backend process. Next.js inlines every
`NEXT_PUBLIC_*` variable into the client bundle, so the frontend is given exactly one variable and
it is a URL. `.gitignore` needs `backend/var/`, `runs/`, `frontend/.next/` and
`frontend/.env*.local` — the current file has a stale `ui/.next/` entry.

### 3.3 Regenerating `.env.example`

The committed example is out of date: it lacks `TG_GRAPHNAME`, `TG_GS_PORT` and `TG_API_TOKEN`, and
predates OpenAI entirely.

```bash
cat > .env.example <<'EOF'
# Copy to .env and fill in. Never commit .env.
TG_HOST=https://tg-xxxxxxxx.i.tgcloud.io
TG_SECRET=your-savanna-secret
TG_GRAPH=GRAPH_GOA
TG_RESTPP_PORT=443
TG_GSQL_PORT=443
TG_CHUNK_ROWS=120000

OPENAI_API_KEY=sk-...
OPENAI_MODEL=
OPENAI_EMBEDDING_MODEL=
OPENAI_EMBEDDING_DIMENSIONS=256

API_HOST=127.0.0.1
API_PORT=8000
CORS_ORIGINS=http://localhost:3000

# --- managed by scripts/setup_mcp.py: tigergraph-mcp reads these ---
TG_GRAPHNAME=GRAPH_GOA
TG_GS_PORT=443
TG_API_TOKEN=
EOF
```

## 4. Local setup

**From a clean clone to a running system.** The repo currently has **no dependency manifest** — no
`requirements.txt`, no `pyproject.toml` — and no virtualenv. Creating both is task B1.

```bash
# 1. Python environment — 3.12; uv is optional but fast
uv venv --python 3.12 .venv
VIRTUAL_ENV=.venv uv pip install -e "backend[dev,scripts]"
#   or: python3.12 -m venv .venv && .venv/bin/pip install -e "backend[dev,scripts]"

# 2. Credentials
cp .env.example .env                   # fill TG_HOST, TG_SECRET, OPENAI_API_KEY,
                                       # OPENAI_MODEL, OPENAI_EMBEDDING_MODEL

# 3. Check every assumption a run makes, and see which one is false
.venv/bin/python -m sentinel doctor
# the workspace auto-stops; the first call takes ~45 s and the repository retries

# 4. Backend
.venv/bin/python -m uvicorn api.main:app --reload --port 8000 --app-dir backend

# 5. Frontend
cd frontend && npm install && npm run dev      # http://localhost:3000
```

Ten-minute check: steps 1–3 are about four minutes on a warm cache, step 4 is instant, step 5's
`npm install` is the long pole.

## 5. Data and graph operations

### 5.1 Rebuilding the graph

The staged data is committed, so the 708 MB source is not needed.

```bash
python scripts/load_to_tigergraph.py            # schema, jobs, load, verify — ~1.3 min
python scripts/load_to_tigergraph.py --verify-only
```

Flags: `--schema-only`, `--data-only`, `--only <job>`, `--verify-only`, `--drop`.

Verification asserts exact counts, not `> 0`:

```
Customer 13553 · Card 14317 · Transaction 590742 · DeviceProfile 9704
BillingRegion 332 · EmailDomain 60 · ProductCode 5 · ClosedCase 5565 · Alert 20
OWNS 14317 · MADE 590742 · NEXT 576425 · OF_PRODUCT 590742 · FROM_DEVICE 140784
BILLED_IN 525003 · PURCHASER_EMAIL 496262 · RECIPIENT_EMAIL 137453
CC_INVOLVES 14955 · CC_ON_CARD 5565 · CC_CONNECTED_TO 92 · ALERT_ON_CARD 20 · ALERT_ON_TXN 20
```

### 5.2 Regenerating staging from source

Only needed if the projection changes. Download `transactions.csv` to the repo root, then:

```bash
python scripts/prepare_data.py                  # → data/staging/*.csv, ~10 s, 5 integrity checks
```

**Known defect:** `eval/fit_elt.py` reads `data/staging/transactions.csv`, which exists on disk only
as `transactions.csv.gz`. The loader handles the `.gz` fallback; the fitter does not. Refitting
`elt.json` as written would fail — fix the path before attempting a refit.

### 5.3 Installing GSQL queries

```bash
python scripts/install_queries.py --create-only   # syntax check, fast
python scripts/install_queries.py                 # create + INSTALL (the slow step)
python scripts/install_queries.py --list
```

`INSTALL` compiles. The existing 16 took **4 minutes**. Batch every new query into one file and one
install pass; do it early and in parallel with other work.

Note that `graph/queries/sentinel_queries.gsql` hard-codes `FOR GRAPH GRAPH_GOA` in every query,
unlike `schema.gsql` which uses the `@GRAPHNAME@` placeholder. The installer strips `//` comments
because the parser rejects comments containing a quote.

### 5.4 GraphRAG ingestion

```bash
python -m sentinel ingest --what policy      # 46 chunks parsed out of Guide.md, seconds
python -m sentinel ingest --what cases       # 5,565 ClosedCase.emb, ~3 min
python -m sentinel ingest --what all         # both; idempotent, so a re-run is free
python -m sentinel ingest --what all --force # re-embed rows that are already current
```

Cost and volume: closed-case notes are 133–437 chars (median 335), roughly 500 k tokens total —
about **$0.01** on `text-embedding-3-large` truncated to 256 dimensions, which retains more
quality than `-3-small` at the same width. At `dimensions=256` the write-back is about **25 MB**
of JSON, batched 200 vertices per POST; at 1,536 it would be ~130 MB. The ingest is idempotent, so
an interrupted run resumes.

Verify:

```bash
curl -s -X POST "$TG_HOST/restpp/builtins/$TG_GRAPH" \
  -H "Authorization: Bearer $TG_API_TOKEN" \
  -d '{"function":"stat_vertex_number","type":"PolicyDoc"}'
# expect 46
```

### 5.5 Running investigations

```bash
python -m sentinel run --case HHG-003            # one case, ~20 s
python -m sentinel run --all                     # the benchmark, ~6 min
python -m sentinel run --all --out /tmp/rehearse # a rehearsal that does not touch cases/
python -m sentinel run --case HHG-003 --no-write # skip the graph write-back
python -m sentinel validate                      # 20 files, shape + policy + graph ids
python -m sentinel validate --no-graph           # shape only, no credentials needed
python -m sentinel explore rings                 # the two-hop sweep, into exploration/
python -m eval.validate                          # the organiser-facing mirror of the same contract
```

The batch always runs in `Alert.opened_at` order, so HHG-017 (2016-11-12) runs first and HHG-004
and HHG-011 (2016-12-29) run last. Each case's `FraudCase` is written before the next starts,
which is what lets a late case retrieve an early one. The order is not a flag: memory that
compounds backwards is a leak, so there is nothing to turn off.

`run --all` prints the batch report and writes it to `runs/<run_id>/report.json` with the verdict
mix, the block and SAR rates, and a warning line for each of the four ratios that is out of band.
The per-case event journal lands in `runs/<run_id>/trace/<case>.jsonl`, and an answer that fails
validation goes to `runs/<run_id>/quarantine/` with its findings rather than into `cases/`.

### 5.6 Tests

```bash
pytest backend/tests/unit backend/tests/integration -q      # no credentials needed
pytest backend/tests -m live -q                              # live graph, ~40 s
cd frontend && npm test
```

## 6. API reference

Base path `/api`. Errors are `application/problem+json`. Every mutating endpoint accepts
`Idempotency-Key`. `X-Sentinel-Role` selects the principal (`analyst` | `team_lead` |
`fraud_manager`); demo-grade authentication over real authorization.

| Method & path | Request | Response | Errors |
|---|---|---|---|
| `GET /api/health` | — | `{status, version, uptime_s}` | — |
| `GET /api/ready` | — | `{ok, checks[]}` | 503 `not_ready` |
| `GET /api/meta` | — | enums, routing table, thresholds, tool names, roles | — |
| `GET /api/cases` | `?status&verdict&trigger_type&sort&q&limit&offset` | `{items: CaseSummary[], total}` — **all 20**, including cases with no answer yet | — |
| `GET /api/cases/{case_id}` | — | `{alert, answer, run, validation, trace_available, actions}` | 404 |
| `GET /api/cases/{case_id}/answer` | — | the answer file **verbatim** | 404 `answer_not_written` |
| `GET /api/cases/{case_id}/trace` | `?run_id` | `{steps, postings, trajectory, tool_calls, tokens, latency_s}` | 404 |
| `GET /api/cases/{case_id}/validation` | `?graph=false` | `{ok, errors[], warnings[]}` | 404 |
| `POST /api/cases/{case_id}/write-to-graph` | `{force}` | 202 `{graph_case_id, vertices, edges, embedded}` | 404, 409, 503 |
| `POST /api/investigations` | `{case_id, mode, force, orchestrator?}` | 202 `{run_id, stream_url, events_url}` | 404, 409 `run_already_active`, 429 |
| `GET /api/investigations` | `?case_id&status&limit` | `{items: RunSummary[]}` | — |
| `GET /api/investigations/{run_id}` | — | `{run_id, case_id, status, step, counters, budget, error}` | 404 |
| `GET /api/investigations/{run_id}/events` | `Last-Event-ID` \| `?last_event_id&speed&role` | `text/event-stream` | 404, 503 |
| `GET /api/investigations/{run_id}/events.json` | `?from&to` | `{events[], next}` | 404 |
| `POST /api/investigations/{run_id}/cancel` | — | 202 | 404, 409 |
| `GET /api/cases/{case_id}/actions` | `?phase` | the action plan with server-recomputed routes | 404 |
| `POST /api/actions/execute` | `{case_id, action, phase, payload, on_denied}` | execution record | **403 `forbidden_route`**, 404, 409, 422 |
| `GET /api/actions/executions` | `?case_id&status&limit` | `{items[]}` | — |
| `GET /api/approvals` | `?status&route&case_id` | `{items[], counts}` | — |
| `POST /api/approvals/{id}/decision` | `{decision, note}` | `{approval, execution?}` | 403 `role_insufficient_for_approval`, 404, 409 |
| `POST /api/approvals/{id}/approve` | `{note}` | alias of the above | as above |
| `GET /api/audit` | `?case_id&actor&action&limit&offset` | `{items[], total}` | — |
| `GET /api/graph/cases/{case_id}` | `?depth&include_ring&max_nodes` | `{nodes[], edges[], truncated, legend}` | 404, 503 |
| `GET /api/cases/{case_id}/sar` | — | the SAR — **200 even when `file: false`** | 404 |
| `GET /api/cases/{case_id}/sar.txt` | — | `text/plain` rendered filing | 404 |
| `GET /api/cases/{case_id}/memory` | — | `{prior_closed_cases[], sentinel_cases[], cited[], retrieval{structural, vector}}` | 404, 503 |
| `POST /api/benchmark/runs` | `{case_ids?, order, concurrency, mode}` | 202 `{batch_id, stream_url, case_ids[]}` | 409 |
| `GET /api/benchmark/report` | — | verdict mix, block rate, SAR rate, probability histogram, warnings | — |

Three deliberate choices:

- **The answer endpoint returns the file naked**, not wrapped, so the download button and any diff tool see exactly what `eval/validate.py` sees.
- **The SAR endpoint returns 200 with `file: false`**, because `sar.reason` for *not* filing is a graded field and a 404 would hide it.
- **A denied execute returns 403 *and* idempotently enqueues the approval**, returning `approval_id` inside the problem envelope. Unusual REST, taken because the exit test is one click from blocked to inbox. `on_denied: "reject"` opts out.

### 6.1 The 403 envelope

```jsonc
// HTTP/1.1 403 ; Content-Type: application/problem+json
{
  "type": "https://sentinel.local/problems/forbidden-route",
  "title": "Action requires approval",
  "status": 403,
  "code": "forbidden_route",
  "detail": "BLOCK_CARD routes to L2 at exposure $2,680.43. Role 'analyst' may execute ['auto'] only.",
  "action": "BLOCK_CARD",
  "required_route": "L2",
  "your_role": "analyst",
  "roles_that_can_approve": ["fraud_manager"],
  "approval": { "approval_id": "apr_01J8…", "status": "pending", "href": "/api/approvals/apr_01J8…" },
  "request_id": "req_7f3a…"
}
```

## 7. SSE event reference

```
retry: 2000

id: 1
event: run.started
data: {"v":1,"seq":1,"run_id":"…","case_id":"HHG-003","type":"run.started","at":"…","step":null,"payload":{…}}

: keepalive
```

- `id:` is `seq` — a monotonic integer per run from 1, so replay is `WHERE seq > ?`.
- Keepalive is an SSE **comment** every 15 s: not journaled, not delivered, cannot advance `seq`.
- Terminal events `run.completed` / `run.failed` close the connection.
- The journal is written **before** publish, so a subscriber can never be ahead of it.

| Event | Key payload fields |
|---|---|
| `run.started` | `case_id, trigger_type, trigger_text, flagged_txn_id, card_id, customer_id, alert_risk_score (null when the -1 sentinel), prior, mode, orchestrator, budget` |
| `step.started` | `step, name, title, agent` |
| `step.completed` | `step, name, elapsed_s, tool_calls_in_step, postings_in_step` |
| `tool.called` | `step, tool, ref, params, entity_ids, elapsed_s, ok, error, summary` |
| `evidence.posted` | `step, feature, present, group, claim, source, ref, entity_ids, lr, log_lr, p_before, p_after, capped` |
| `retrieval.completed` | `step, kind, strategy, query, hits[{id, kind, score, title, snippet, ref}]` |
| `policy.evaluated` | `step, phase, fraud_probability, exposure_usd, verdict, independent_support, recommendations[], gates_applied[], sar, stop` |
| `evidence.requested` | `step (== asked_after_step), request_index, type, rationale, assumed_response, assumption_basis, branch, log_lr_applied` |
| `llm.completed` | `step, purpose, model, prompt_tokens, completion_tokens, total_tokens, usd, elapsed_s` |
| `budget.updated` | the budget snapshot |
| `verdict.reached` | the whole case shape plus `trajectory` |
| `case.written` | `graph_case_id, vertices_upserted, edges_upserted, edge_types, embedded, answer_path` |
| `validation.completed` | `ok, errors[], warnings[], graph_checked` |
| `run.completed` | `tool_calls, tokens, latency_s, validation_ok, answer_url` |
| `run.failed` | `code, message, step, recoverable` |

**The `EventSource` trap.** The browser's native `EventSource` cannot send custom headers, so it can
carry neither `X-Sentinel-Role` nor `Last-Event-ID` on a first connect, and it reconnects forever
past a terminal event. Use `fetch` + `ReadableStream`. The API also accepts `?role=` and
`?last_event_id=` as query fallbacks, and `Last-Event-ID` is in the CORS allow-list.

## 8. Tool reference

Sixteen installed queries, all verified executing live. Signatures, return keys, the question each
answers and measured latencies are catalogued in [LLD.md §3.3](LLD.md#33-the-tool-catalogue);
ten additions are specified in [LLD.md §3.4](LLD.md#34-new-queries-task-g1-one-install-pass).

Calling one directly:

```bash
set -a && . ./.env && set +a
curl -s --get "$TG_HOST/restpp/query/$TG_GRAPH/region_novelty" \
  -H "Authorization: Bearer $TG_API_TOKEN" \
  --data-urlencode "c_in=C08623-K2" \
  --data-urlencode "region=330.0" \
  --data-urlencode "as_of=2016-12-10 13:01:21"
# → prior_txns_in_region 42, total 51, prior_txns_on_card 980,
#   first_seen_in_region 2016-07-09 23:53:35, in_person_elsewhere_24h 10
```

Over raw REST a `VERTEX<T>` parameter takes a **bare id**. Through pyTigerGraph it must be a
**1-tuple** (`{"c_in": ("C08623-K2",)}`) or the client silently falls back to a deprecated GET path.

**Five landmines, ranked by cost:**

1. **`center` / `as_of` must come from `Transaction.ts`, never `Alert.opened_at`.** The lag is +1 h to +6 h on all 20 alerts and never 0.
2. **`Alert.risk_score == -1` on 9 of 20 alerts** — it is the missing-numeric sentinel, not a score. The model score is on the transaction. Feeding the alert value in puts nine cases in `risk_00_30` (LR 0.372) instead of their real band.
3. **`-1.797693134862316e+308`** from empty `MaxAccum<DOUBLE>` in `card_testing_probe.largest_purchase_after` and `velocity_probe.max_risk_in_window`. Normalise to `None`.
4. **`"1970-01-01 00:00:00"`** from empty DATETIME accumulators in `region_novelty`. Normalise to `None`, or the model will report the card was first seen there in 1970.
5. **Alias-prefixed keys** on 12 of 16 queries (`T.txn_id`, `SIB.card_id`, `C2.@shared`), bare names on 4. One normalizer, applied once.

### 8.1 MCP

```bash
pip install tigergraph-mcp                 # installed; setup_mcp points .mcp.json at the venv binary
python scripts/setup_mcp.py --refresh      # mint a token into the managed .env block
python scripts/setup_mcp.py --check        # handshake, tool list, then region_novelty == 42
```

`.mcp.json` launches a bare `tigergraph-mcp`, which loads `.env` from the working directory and
reads **its own** variable names (`TG_GRAPHNAME`, `TG_GS_PORT`, `TG_API_TOKEN`). The server exposes
69 tools. MCP is a required component of the brief and a genuine demonstration that the graph is
agent-accessible over a standard protocol — but it adds nothing the typed Python repository does
not already have, so the agent's real traffic goes through the repository where the refs, the query
log and the normalisation live.

## 9. LLM usage policy

| Purpose | Called by | Output schema | Notes |
|---|---|---|---|
| `plan` | `PlannerAgent` | `{tools: [{name, args, why}]}` | Tool names validated against the registry |
| `claims` | `AssessmentAgent` | `{pattern, pattern_description, claims[], summary}` | `pattern` constrained to the 7 enum values |
| `exonerate` | `DevilsAdvocateAgent` | `{exonerating[], rebuttal}` | May only cite postings and refs that already exist |
| `sar_narrative` | `NarrationAgent` | `{narrative}` | Grounded in the retrieved FinCEN chunk; 6–12 sentences |
| `what_changed` | `NarrationAgent` | `{what_changed}` | Two sentences maximum |
| `assumed_response` | `EvidenceRequestStep` | `{assumed_response}` | Written **from** the simulator's basis; the branch is already decided |

Rules: temperature 0 and a fixed seed on every call; structured output only — a mismatch is a retry
with the validation error fed back, not a parse; every call records `prompt_tokens`,
`completion_tokens` and `usd` through `CostMeter`, and the total fills `answer.tokens`.

**Cost estimate.** Six calls per case at roughly 4–8 k prompt tokens and 300–900 completion tokens
is on the order of $0.05–$0.30 per case with a current mid-tier model, so the 20-case benchmark
lands around $1–$6. Embeddings are about $0.01 once. `MAX_USD_PER_RUN = 1.50` is a ceiling, not a
target; exceeding it ends the run as `budget_exceeded` rather than quietly costing more.

## 10. Coding standards

**Python**

- Full type hints; `from __future__ import annotations` at the top of every module.
- Pydantic v2 models at every boundary; `@dataclass(frozen=True, slots=True)` for internal value objects.
- Every class carries a docstring naming its **responsibility** and its **collaborators** — the existing `policy.py` already does this and it is why it reads well.
- Dependencies are injected through `__init__`; nothing constructs its own collaborators, so every class is testable without a network.
- No `print()` in library code. No `sys.exit` outside `cli/`.
- Never a bare `except Exception` that flattens distinct failures into one message.
- Never interpolate a value into GSQL.
- `ruff check` and `ruff format`; `mypy --strict` on `backend/sentinel`.

**TypeScript**

- `strict: true`; no `any` in `src/lib`.
- Enums and unions imported from `lib/generated/contract.ts`; never hand-typed.
- Components take typed props; variants through `class-variance-authority`.
- No inline hex colours — tokens only. A hex literal in a component is a review rejection.

**Commits**

Conventional-commit prefixes (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`). A commit
that touches `cases/` must state which validator run backs it.

## 11. Testing and CI

The test matrix is in [LLD.md §15](LLD.md#15-test-matrix). What runs when:

| Trigger | Suite | Needs credentials |
|---|---|---|
| Every commit | `pytest backend/tests/unit backend/tests/integration` + `ruff` + `mypy` + `npm test` + `tsc --noEmit` | no |
| Every commit touching `cases/` | `python -m eval.validate --no-graph` | no |
| Before a push | `pytest -m live` + `python -m eval.validate` | yes |
| Before submission | the full matrix plus a manual pass of the Step 8 exit test in the browser | yes |

```yaml
# .github/workflows/ci.yml — sketch
jobs:
  backend:
    steps:
      - run: pip install -e "backend[dev]"
      - run: ruff check backend && ruff format --check backend
      - run: mypy --strict backend/sentinel
      - run: pytest backend/tests/unit backend/tests/integration -q
      - run: python -m eval.validate --no-graph
  frontend:
    steps:
      - run: npm ci --prefix frontend
      - run: npm run --prefix frontend typecheck
      - run: npm run --prefix frontend test
```

Live tests are excluded from CI by `-m "not live"`, because CI has no Savanna credentials and the
workspace auto-stops.

## 12. Observability and debugging

**Structured logs.** JSON lines with `request_id` and `run_id` from a contextvar. One access-log
line per request. Every tool call and LLM call logs its name, elapsed time and cost.

**The journal is the debugger.** Every run's events are in SQLite:

```bash
sqlite3 backend/var/sentinel.db \
  "SELECT seq, type, step, json_extract(payload,'$.tool') FROM run_event WHERE run_id=? ORDER BY seq"
```

**Replaying a run:**

```bash
curl -N "http://localhost:8000/api/investigations/$RUN/events?from=0&speed=3"
```

**Debugging a wrong answer**, in order: read the trace (`/api/cases/{id}/trace`) for the ledger
postings and find which posting moved the probability wrongly; check the tool result behind that
posting's `ref`; re-run that query directly over REST; if the fact is right and the reading is
wrong, the bug is in `FeatureExtractor`, not in the model.

## 13. Operational runbook

### 13.1 Before a demo

```bash
# 1. Wake the workspace — it auto-stops and takes ~45 s
curl -s -o /dev/null -w "%{http_code}\n" "$TG_HOST/api/ping"

# 2. Refresh the bearer token (expires 2026-09-27)
python scripts/setup_mcp.py --refresh

# 3. Confirm the graph
python -c "from sentinel import config as cfg; c=cfg.connect(); print(c.getVertexCount('Transaction'))"

# 4. Pre-warm every case the demo will show
for c in HHG-019 HHG-014 HHG-003; do curl -s "http://localhost:8000/api/cases/$c" > /dev/null; done

# 5. Switch the orchestrator to replay for recording
#    Container: orchestrator = ReplayOrchestrator(...)
```

### 13.2 Known failure modes

| Symptom | Cause | Fix |
|---|---|---|
| HTML page instead of JSON, title "Starting workspace" | Workspace auto-stopped | Wait ~45 s; the repository retries automatically. Never treated as an error |
| `401` / `403` from REST++ | `TG_API_TOKEN` expired (2026-09-27) | `python scripts/setup_mcp.py --refresh`. The Python path mints from `TG_SECRET` and is unaffected |
| `ENOENT: tigergraph-mcp` | Binary not on PATH | `pip install tigergraph-mcp`, then point `.mcp.json` at the venv binary |
| `RuntimeError: elt.json is missing` | `ledger.py` moved without `elt.json` | They live together; the path is `Path(__file__).with_name("elt.json")` |
| Refit fails on a missing CSV | `fit_elt.py` reads `data/staging/transactions.csv`, only the `.gz` is committed | Gunzip, or add the `.gz` fallback the loader already has |
| Every case escalates, nothing blocks | `CaseState.independent_signals` left at its default of 1, so R1's gate strips every block | `CaseStateBuilder` must set it from `ledger.independent_support()` |
| Ring of 25 cards on an in-person transaction | `ring_expand` called without `max_device_cards` | Always pass the gate. 116 profiles carry 24,653 of the card links |
| SAR filed on most cases | R2 read without the §3a gate | `SarPolicy` requires confirmed-or-strongly-suspected **and** a trigger condition |
| `final` differs from `initial` with no evidence request | Non-deterministic action ordering between the two policy calls | The validator makes this a hard error; assert deep equality in the assembler |

## 14. Troubleshooting

| Message | Meaning | Action |
|---|---|---|
| `Transaction '3530164' does not exist in the graph` | In the current validator this is also what a network failure looks like | Use the new checker, which separates the two; then check the id |
| `-1.797693134862316e+308` in a response | Empty `MaxAccum<DOUBLE>` | `ResponseNormalizer.sanitize` should have made it `None`; a contract test asserts no response contains it |
| `1970-01-01 00:00:00` in a claim | Empty DATETIME accumulator reached the model | Same |
| `Semantic Check Fails` on install | GSQL parse error; the parser also rejects `//` comments containing a quote | The installer strips comments; check for reserved words — `proxy` and `Case` are reserved, hence `proxy_flag` and `FraudCase` |
| `budget_exceeded` | 25 tool calls, 1 evidence round, tokens, USD or wall clock | Read the budget snapshot on the failure event; it names which ceiling |
| `role_insufficient_for_approval` | A team lead tried to approve an L2 | Switch to `fraud_manager`; the route is re-asserted at decision time |
| `no evidence was requested, so 'final' must equal 'initial'` | Deep-equality failure including reason strings | The two policy calls must be given identical state when no evidence round ran |
