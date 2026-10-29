# Running Sentinel on the Round 2 pack — from a clean clone

Every answer file is produced by this code, at the frozen commit, with one
command. The graph is the source of truth; the run fails closed if it is down.

## 1. Setup (once, ~10 minutes)

```bash
git clone -b sentinel-v2 https://github.com/r-rishit27/hacker_house.git && cd hacker_house

# Python 3.12
python -m venv .venv && .venv/bin/pip install -e "backend[dev,scripts]"      # Windows: .venv\Scripts\pip

cp .env.example .env
#   TG_HOST, TG_SECRET (or TG_KEY), TG_GRAPH=GRAPH_GOA      TigerGraph Savanna workspace
#   OPENAI_API_KEY                                          decision model, tricky cases only
#   OPENAI_DECISION_API=decisions  OPENAI_DECISION_MODEL=gpt-6-luna

# Stage July 2016 - January 2017 (data/round2 holds the January files and the
# replacement closed_cases_history; data/staging holds July - December).
.venv/bin/python scripts/stage_january.py           # -> data/lab.duckdb, data/staging_r2/   (~1 min)
```

Only if the graph has to be (re)built — `GRAPH_GOA` is already loaded on the
team workspace:

```bash
STAGING_DIR=data/staging_r2 .venv/bin/python scripts/load_to_tigergraph.py        # schema, jobs, data, verify (~7 min)
.venv/bin/python scripts/install_queries.py                                       # 26 queries        (~7 min)
.venv/bin/python scripts/install_queries.py --file queries/sentinel_queries_v3.gsql  # 7 typology queries (~2 min)
```

`load_to_tigergraph.py` ends with a verification of every vertex and edge count
against the staging files; it prints `ALL CHECKS PASSED`.

The Savanna workspace auto-stops when idle. The first request after that returns a
"Starting workspace" page for ~20 s; send one request a minute before a run.

## 2. One case (the drill command)

```bash
cd backend
../.venv/bin/python -m sentinel.typologies.run --pack ../case_pack_final.csv --case HHF-007 --out ../cases
```

Writes `cases/HHF-007.json` and appends a row to `run_log.csv` (next to `cases/`).
Exit code 0 = written; 1 = a case failed and was not written; 2 = the graph is
unreachable and nothing was written.

| flag | meaning |
|---|---|
| `--source graph` (default) | read TigerGraph `GRAPH_GOA` |
| `--source lab` | read the local DuckDB mirror; every evidence ref then says so |
| `--no-model` | deterministic layer only; cases it cannot settle become `uncertain` + escalation |

## 3. The whole pack

```bash
cd backend && ../.venv/bin/python -m sentinel.typologies.run --pack ../case_pack_final.csv --out ../cases
```

Prints median and p95 latency, graph queries and model tokens; `run_log.csv` has
them per case. Check the files with the organisers' grader:

```bash
python grade.py --teams-dir teams --case-pack case_pack_final.csv --transactions data/round2/transactions_2017q1.csv.gz --out report.json
```

## 4. Reproduce every number in the write-up

```bash
python -m eval.replay_round1                 # Round 1's 20 against their real outcomes
python -m eval.overfit_check [--model]       # held-out cleared alerts, held-out frauds, January groups, ablation
python -m eval.episode_check                 # episode scoping vs 300 labelled episodes
python -m eval.graph_parity                  # graph vs mirror: identical decisions on all 20
python -m eval.backtest_typologies           # each detector on closed cases, controls, January
cd backend && python -m pytest tests/unit/test_adjudicator.py tests/unit/test_decisions_backend.py -q
```

## 5. What each answer field honestly reports

- `tool_calls` — graph queries this case made (or mirror queries with `--source lab`).
- `tokens` — tokens of the decision-model call. It is the only model this pipeline
  calls; a case settled by the rules reports 0, because no model ran.
- `latency_s` — wall time for the case: graph, model, policy engine, validation.
- Replies to evidence requests are simulated; each states its assumption.
