# Sentinel — Agentic Fraud Investigation on TigerGraph

**An agent that investigates card fraud on a graph, and knows when not to block.**

Sentinel reads a bank's transaction graph in TigerGraph, finds fraud patterns nobody wrote down, refuses to harm a
customer on a single odd signal, asks a person before it blocks anything, and writes every step into a
tamper-evident record a regulator can check. Anyone on the team can ask it questions in plain words (**Ask
Sentinel**), and a new bank with a completely different graph schema can be brought to the same detectors through
one reviewed, signed mapping (**bank onboarding**).

Built for the TigerGraph Agentic Fraud Investigation challenge, Hacker House Goa, by **Team CollabUp**.

| | |
|---|---|
| 🔗 **Live app** | https://hackerhouse.collabup.co.in |
| 📘 **API docs** | https://apihacker.collabup.co.in/api/docs |
| 💻 **Code** | https://github.com/r-rishit27/hacker_house/tree/sentinel-v2 |
| 🧭 **Design (HLD + LLD)** | [docs/design/ARCHITECTURE.md](docs/design/ARCHITECTURE.md) · one page: [docs/design/SYSTEM_ON_ONE_PAGE.md](docs/design/SYSTEM_ON_ONE_PAGE.md) |
| ✍️ **Write-up** | [Sentinel: an agentic fraud investigator on TigerGraph that knows when *not* to block](https://medium.com/@r.rishit27/sentinel-an-agentic-fraud-investigator-on-tigergraph-that-knows-when-not-to-block-41b8e706116e) |

The TigerGraph Savanna workspace auto-stops when idle. The first request after a quiet spell takes about 45 seconds
while it wakes; after that it is warm.

---

## Sign in and look around

Open the app, sign in with any of these, and you see exactly what that role may see and do. The server enforces
every permission; the screens only hide what a role cannot use.

| Username | Role | Password | Lands on |
|---|---|---|---|
| `analyst.raj` | Analyst | `DhQoOakT2y5g` | Case portal |
| `lead.priya` | Team lead | `NKzeymnl_qHu` | Case portal |
| `manager.anika` | Fraud manager | `yDme8E6s7CVN` | Case portal |
| `examiner.ines` | Regulator | `0xVI4eIvFI7p` | Regulator overview |

These are **demo accounts, published on purpose** so a reviewer can sign in. They hold demo data only. Five wrong
passwords lock an account for 15 minutes. To create them on another deployment, add each one with
`cd backend && python -m api_v2.users add --user <username> --role <role>` and enter the password above when asked.
A deployment of an older build has no sign-in page.

### What each role sees and can do

| | Analyst | Team lead | Fraud manager | Regulator |
|---|:-:|:-:|:-:|:-:|
| Alert queue, start an investigation | ✓ | ✓ | ✓ | – |
| Case file: timeline, probability trajectory, graph, evidence, SAR | ✓ | ✓ | ✓ | ✓ (read-only) |
| A case's step-by-step audit trail | ✓ | ✓ | ✓ | ✓ |
| Approve or reject **L1** actions (block card, decline) | – | ✓ | ✓ | – |
| Approve or reject **L2** actions (file report, block all cards) | – | – | ✓ | – |
| Full audit trail across all cases, verify the hash chain | – | – | ✓ | ✓ |
| Change record (data drift and its approval) | – | – | ✓ | ✓ |
| Regulator overview and examiner walkthrough | – | – | ✓ | ✓ |
| Who has access, and with which role | – | – | ✓ | – |
| **Ask Sentinel** (answers only from what this role may see) | ✓ | ✓ | ✓ | ✓ |

A five-minute tour:

1. **As `analyst.raj`:** open HHG-014 in the queue. The timeline shows each finding moving the probability; the graph
   pane shows the device ring. Try to approve the block: the server refuses, and the refusal is in the audit trail.
2. **Click the tiger button (bottom right) — Ask Sentinel.** Ask *"Why was HHG-014 called fraud? Cite the evidence."*
   Every claim links to its source. Then ask *"Who failed to sign in?"*: the analyst role cannot see the access log,
   so the answer says so and names the missing permission, without revealing anything from it.
3. **As `lead.priya`:** approve the L1 block on HHG-014. The L2 report stays waiting for a fraud manager.
4. **As `manager.anika`:** approve the report. Open *Regulator overview* from the masthead.
5. **As `examiner.ines`:** walk an investigation step by step, check the approval chain, and press *Re-verify chain*
   to recompute every hash in your own browser.

---

## What is in it

| | |
|---|---|
| **Investigation on the graph** | 8 detectors for patterns nobody wrote down (structuring under a threshold, a device ring adopted by many cards, a known ring device returning, dormant cards waking, escalating charges, micro-charges, many cards paying one recipient, card testing). Rings are found by label propagation inside TigerGraph |
| **History checks** | Six look-alike tests taken from the bank's own cleared cases: an anomaly the card's history explains does not count against the customer. This is what took innocent customers blocked from 4 to 0 |
| **Rules first, model second** | Decisive detectors, customer denials, explained alerts and one-weak-signal alerts are settled by rules. Only what is left goes to the decision model, which returns five probabilities, never prose, and may not block a single charge on its own |
| **Policy engine** | The bank's Fraud Policy as tested code: R1–R10, the §3a report rule, approval routes auto / L1 / L2. It runs before and after an evidence request, so a change of recommendation is explained |
| **Human approval** | L1 actions wait for a team lead, L2 for a fraud manager. Every grant and refusal is recorded |
| **Containment planner** | For a ring, blocks the cards whose exposure is worth more than disrupting a cardholder and monitors the rest |
| **Write-back** | Each case becomes a `FraudCase` in the graph, marked saved only after it reads back the same |
| **Audit trail** | Append-only, every entry carries the SHA-256 of the one before; editing any entry breaks the chain exactly there |
| **One sign-in** | Username and password (salted PBKDF2, lock-out), one signed session read by every service; the role in it decides where you land and what each screen and API call allows |
| **Ask Sentinel** | Plain-language questions over investigations, audit trails, the live graph, closed-case vectors, the change record, project docs and your own uploaded documents. Three guardrails: entitlement before ranking, untrusted text read as data, every claim cited and checked. Read-only; every question is in the audit trail |
| **Regulator view** | Every investigation, its approval chain, the change record, an examiner walkthrough, and in-browser hash verification; exportable as one read-only file |
| **Bank onboarding** | A new bank's graph schema (ISO 20022, BIAN, Temenos T24 tested) is mapped onto Sentinel's canonical model: a model proposes, code validates every name and path, a person reviews, a team lead signs. The unchanged detectors then run on that bank's own graph |
| **Restricted MCP tools** | Five read-only tools for an assistant or IDE: card history, device history, ring components, investigate an alert, verify the audit chain. No raw query, no write |

---

## Results

Round 1's twenty alerts, scored against their real outcomes and **run live on the graph** (decision model, vector
precedent, write-back and audit trail on):

| | Round 1 | Now |
|---|---|---|
| Correct | 6 / 20 | **16 / 20** (3 uncertain and escalated, 1 wrong) |
| Innocent customers blocked | 4 | **0** |
| Written to the graph and read back | — | 20 / 20 |

- On held-out cleared alerts the rules call **2.0%** of innocent customers fraud; 299 / 300 held-out frauds kept.
- Graph and local mirror give identical decisions on all 20. `python -m eval.self_grade`: 0 hard, 0 policy findings.
- The largest card went from 373 graph queries and 170 s to 15 queries and 17 s with one batched read.
- Ask Sentinel: 0 sources outside the asking role ever sent to the model (tested).
- Bank onboarding, three test banks with unrelated schemas: 79 / 79 mapping items right after review, 0 wrong;
  the same structuring burst found **live** in all three, and the device ring in the one that records devices.

The evidence behind each number: [docs/ROUND2_FINDINGS.md](docs/ROUND2_FINDINGS.md),
[docs/INNOVATION.md](docs/INNOVATION.md), [docs/ONBOARDING.md](docs/ONBOARDING.md).

---

## Run it yourself

Requirements: Python 3.12 with the backend packages, Node 20+, and a `.env` at the repo root (`cp .env.example .env`):
`TG_HOST`, `TG_SECRET` (or `TG_KEY`), `TG_GRAPH=GRAPH_GOA`, `OPENAI_API_KEY`, `OPENAI_MODEL`,
`OPENAI_DECISION_MODEL`, `SENTINEL_AUTH_SECRET`.

```bash
git clone -b sentinel-v2 https://github.com/r-rishit27/hacker_house.git && cd hacker_house
pip install -e "backend[dev,scripts]"          # or into a venv
cp .env.example .env                           # fill in the values above

./run.sh user analyst.raj analyst              # one per login in the table above; asks for the password
./run.sh warmup                                # wake TigerGraph, check counts, prints READY
./run.sh start                                 # sign-in/approvals (8100), console API (8000), console (3000)
# open http://localhost:3000 and sign in
```

Everything else is in one script:

| Command | What it does |
|---|---|
| `./run.sh start` · `stop` · `restart` · `status` · `logs [v2\|api\|web]` | the three services |
| `./run.sh warmup` | wake the TigerGraph workspace and check it |
| `./run.sh pack [case_pack_final.csv]` | answer a whole case pack live on the graph into `cases/`, then self-grade |
| `./run.sh case HHF-007` | answer one case (the drill command) |
| `./run.sh rehearse` | run the frozen code on Round 1's 20 cases |
| `./run.sh grade [dir]` | self-grade answer files |
| `./run.sh test` | unit tests and the frontend typecheck |
| `./run.sh user NAME ROLE` | add a sign-in (asks for the password) |
| `./run.sh snapshot` | export the read-only regulator snapshot |
| `./run.sh onboard propose\|build\|capabilities\|score\|approve\|dryrun …` | onboard a new bank's schema |

The runner on its own:

```bash
cd backend
python -m sentinel.typologies.run --pack ../case_pack_final.csv --case HHF-007 --out ../cases   # one case
python -m sentinel.typologies.run --pack ../case_pack_final.csv --out ../cases                  # the pack
python -m sentinel.typologies.run --bank Bank_CoreT24 --pack ../onboarding/banks/Bank_CoreT24/case_pack.csv --out ../out
```

Exit code 0 = written; 1 = a case failed and was not written; 2 = the graph (or an unapproved bank mapping) refused
and nothing was written. There is no silent fallback to other data. Full detail: [docs/ROUND2_RUN.md](docs/ROUND2_RUN.md).

---

## Architecture

```
Alert ─▶ Validate ─▶ Batched graph read ─▶ 8 detectors ─▶ History checks ─▶ Rules ─▶ (model, tricky cases only)
      ─▶ Policy engine ─▶ Containment ─▶ Human approval ─▶ Write-back & read-back ─▶ Audit chain
                                       Ask Sentinel answers over all of it, within each role
```

Facts come from TigerGraph, belief from detectors and history checks, decisions from rules and a policy engine. The
model only scores what the rules cannot settle, and a person approves every block and every report.

| Service | Port | What |
|---|---|---|
| Console (Next.js) | 3000 | the front door: `/login`, the analyst console, `/regulator`; proxies `/api` and `/v2` so one session cookie reaches both services |
| Console API (FastAPI) | 8000 | cases, runs, traces, actions, graph views |
| v2 service (FastAPI) | 8100 | sign-in, approvals, the audit ledger and change record, Ask Sentinel |
| TigerGraph Savanna 4.x | — | `GRAPH_GOA` (650,938 transactions July 2016 – January 2017, 5,585 closed cases with vectors, 36 installed GSQL queries) plus each onboarded bank's own graph |
| OpenAI | — | the decision model (tricky cases only), embeddings, Ask Sentinel's cited answers; pseudonymous ids only |

The full design, with the system map, six data-flow walkthroughs, the class diagram, deployment and the pillars:
**[docs/design/ARCHITECTURE.md](docs/design/ARCHITECTURE.md)**. Interactive version:
[system map](https://claude.ai/artifact/JYEWEZyCDC1yXwjtYhPzCC) ·
[full design](https://claude.ai/artifact/Em7gXScLV88xp8QYwcu9aL).

### Where the code is

| Path | What it owns |
|---|---|
| `backend/sentinel/typologies/` | The Round 2 decision layer: detectors, history checks, rules, model gate, policy hand-off, containment, write-back, audit ledger, runner |
| `backend/sentinel/onboarding/` | Bank onboarding: schema parser, canonical model, mapping validator, proposer, review and approval, generic adapter, capabilities |
| `backend/sentinel/policy/` | The Fraud Policy as tested code |
| `backend/sentinel/graph/` | TigerGraph access for the console API (token minting for TigerGraph 4.x and 3.x) |
| `backend/api/` | Console API |
| `backend/api_v2/` | Sign-in, roles (`rbac.py`), approvals, regulator view, Ask Sentinel (`assistant.py`), MCP server |
| `frontend/` | The console, sign-in page, Ask Sentinel panel |
| `graph/` | Schema and GSQL queries |
| `onboarding/banks/` | Each onboarded bank: schema, proposed / reviewed / approved mapping, sample case pack |
| `scripts/`, `etl/` | Data staging and graph loading, warm-up, demo users, onboarding sample loader |
| `eval/` | Replay, overfitting check, parity, backtests, prompt-injection attacks, data quality, self-grader |

---

## Rules this repo holds to

**1. No graded field comes from free-form model text.** Transaction ids, amounts, exposure, actions, approval routes
and the report decision come from a GSQL query or the policy engine. Every id in an answer file is checked before the
file is written.

**2. Fail closed.** A graph that does not answer, a malformed alert, a model that times out or an unapproved bank
mapping stops the case and says why; nothing is guessed and nothing is written.

**3. A person approves.** Blocks and reports wait for the right role; data changes and bank mappings wait for a
signed approval bound to exactly what was reviewed.

**4. The public IEEE-CIS / Kaggle files are never opened.** Ids, times and amounts were transformed so outcomes
cannot be looked up there. They were not downloaded, opened or referenced.

---

## The dataset

IEEE-CIS Fraud Detection (Vesta Corporation), repackaged by TigerGraph and extended for Round 2 with January 2017:
650,938 transactions from July 2016 to January 2017, identity records for online transactions, 5,585 closed
investigations (the only place ground truth is written down), and the benchmark alerts. Every transaction carries a
risk score from the bank's model; there is no fraud label.

| Path | What |
|---|---|
| [Guide.md](Guide.md) | The organiser's brief: task, glossary, the five patterns, the Fraud Policy, the answer-file contract |
| [case_pack.csv](case_pack.csv) | Round 1's 20 benchmark alerts |
| `data/round2/` | January 2017 transactions and identity, the replacement closed-case history, the drift report and its signed approval |
| `data/staging/`, `data/staging_r2/` | Loader-ready files; enough on their own to rebuild the graph |

Rebuilding the graph: `scripts/schema_drift.py check` → approve → `scripts/stage_january.py` →
`STAGING_DIR=data/staging_r2 python scripts/load_to_tigergraph.py` → `scripts/install_queries.py`. Step by step in
[docs/ROUND2_RUN.md](docs/ROUND2_RUN.md).

---

## Checks

```bash
./run.sh test                                    # unit tests + frontend typecheck
cd backend && python -m pytest tests/unit -q     # 406 tests, no network or credentials needed
python -m eval.replay_round1                     # Round 1's 20 against their real outcomes
python -m eval.overfit_check                     # held-out alerts, ablation
python -m eval.graph_parity                      # graph vs mirror, all 20
python -m eval.attacks                           # 12 prompt-injection attacks, defended vs undefended
python -m sentinel.onboarding score Bank_CoreT24 # a bank mapping vs its expected mapping
```

---

## Documents

| Path | What |
|---|---|
| [docs/design/ARCHITECTURE.md](docs/design/ARCHITECTURE.md) | HLD and LLD: system map, data flows, classes, deployment, pillars, logs |
| [docs/design/SYSTEM_ON_ONE_PAGE.md](docs/design/SYSTEM_ON_ONE_PAGE.md) | The system on one page |
| [docs/ROUND2_RUN.md](docs/ROUND2_RUN.md) | Running everything, from a clean clone |
| [docs/ROUND2_FINDINGS.md](docs/ROUND2_FINDINGS.md) | What changed for Round 2 and the evidence |
| [docs/ONBOARDING.md](docs/ONBOARDING.md) | Onboarding a new bank's schema, measured on ISO 20022, BIAN and T24 |
| [docs/RESILIENCE.md](docs/RESILIENCE.md) | Resilience statement: failures, drills, what leaves the bank |
| [docs/INNOVATION.md](docs/INNOVATION.md) | The innovation claim and its measurement |
| [docs/ROUND2_CHECKLIST.md](docs/ROUND2_CHECKLIST.md) | Every requirement in the brief and where it is met |
| [docs/TOOLS_AND_MCP.md](docs/TOOLS_AND_MCP.md) | Tools, GSQL queries and the MCP server |
| [docs/system/](docs/system/) | Round 1 PRD, HLD, LLD, technical notes and status |
| [frontend/DESIGN_SYSTEM.md](frontend/DESIGN_SYSTEM.md) | Colour tokens, components, contrast |
| [deploy/Deployment_Doc.md](deploy/Deployment_Doc.md) | Deploying the API and the console |

---

Roughly half the benchmark cases are legitimate, and an agent that blocks everything scores badly. That single
constraint drives most of the design.
