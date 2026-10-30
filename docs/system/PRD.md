# Sentinel v2 — Product Requirements Document

The product definition for the full Sentinel system: an agentic fraud-investigation platform on
TigerGraph that investigates an alert, accumulates calibrated evidence, decides under a
deterministic policy engine when it has enough to act, asks for more when it does not, and produces
a defensible case file, a next-best-action pair and a regulatory filing when policy requires one.

This document supersedes [docs/PRD.md](../PRD.md), which described the v1 build that produced the
graph, the tool layer, the ledger and the policy engine. Everything v1 established as fact still
holds; what changed is recorded in [§1.3](#13-what-changed-from-v1).

| | |
|---|---|
| **Status** | Design complete, build in progress |
| **Owners** | Sentinel team, Hacker House Goa 2026 |
| **Authority** | [Guide.md](../../Guide.md) — the organiser's brief. Where this document and Guide.md disagree, Guide.md wins |
| **Siblings** | [HLD.md](HLD.md) · [LLD.md](LLD.md) · [TECHNICAL.md](TECHNICAL.md) · [EXECUTION_PLAN.md](EXECUTION_PLAN.md) · [frontend/DESIGN_SYSTEM.md](../../frontend/DESIGN_SYSTEM.md) |

---

## Contents

1. [Context](#1-context)
2. [The problem](#2-the-problem)
3. [Product thesis](#3-product-thesis)
4. [Principles](#4-principles)
5. [Users and their jobs](#5-users-and-their-jobs)
6. [The experience, narrated](#6-the-experience-narrated)
7. [Functional requirements](#7-functional-requirements)
8. [Non-functional requirements](#8-non-functional-requirements)
9. [Out of scope](#9-out-of-scope)
10. [Deliverables](#10-deliverables)
11. [Scoring map](#11-scoring-map)
12. [Success metrics](#12-success-metrics)
13. [Assumptions and open questions](#13-assumptions-and-open-questions)

---

## 1. Context

### 1.1 What exists today, verified

Every figure below was read from the live `GRAPH_GOA` workspace (TigerGraph 4.2.5 enterprise) or
from a test run during this session. Nothing is inferred.

| Layer | State |
|---|---|
| Graph | Loaded and verified: Transaction 590,742 · Card 14,317 · Customer 13,553 · ClosedCase 5,565 · DeviceProfile 9,704 · BillingRegion 332 · EmailDomain 60 · ProductCode 5 · Alert 20 · FraudCase 1 · MetaDoc 9 · **PolicyDoc 0** |
| Schema | 12 vertex types, 20 edge types, 13 loading jobs — [graph/schema.gsql](../../graph/schema.gsql) |
| Tools | 16 installed GSQL queries, all executing live at 0.7–1.1 s each — [graph/queries/sentinel_queries.gsql](../../graph/queries/sentinel_queries.gsql) |
| Policy | R1–R10, the 14 actions and the routing table as a pure function — [sentinel/policy.py](../../sentinel/policy.py), **40 tests green** |
| Calibration | 29 likelihood-ratio features fitted on 14,055 confirmed-fraud transactions vs 300,602 card-matched controls — `sentinel/elt.json` |
| Deliverable | **1 of 20** answer files — [cases/HHG-003.json](../../cases/HHG-003.json) |

### 1.2 What does not exist

- **No agent.** No orchestrator, no LLM call anywhere in the repo. `grep -i "openai\|langchain\|anthropic"` over every `.py` file returns zero hits. The tools, the ledger and the policy engine are libraries with no caller.
- **No GraphRAG.** `PolicyDoc` has 0 rows, `ClosedCase.emb` is empty on all 5,565 rows, `FraudCase.emb` is empty. GraphRAG is a **required component** of the brief.
- **No evidence simulator**, so nothing can make `next_best_actions.final` differ from `.initial` — half of a 25 % judging category.
- **No API and no user interface.** A UI is a **required component** of the brief.
- **19 of 20 answer files.**
- No demo video, blog post or social post.

### 1.3 What changed from v1

Four decisions were taken by the product owner and are fixed for this build:

| # | Decision | Consequence |
|---|---|---|
| D1 | **A custom class-based orchestrator over the OpenAI Python SDK**, not LangGraph and not the OpenAI Agents SDK | [docs/BUILD_PLAN.md](../BUILD_PLAN.md) Step 7's LangGraph state machine is replaced by `InvestigationOrchestrator` and its step classes. `OPENAI_API_KEY` is the only model credential; OpenAI supplies both reasoning and embeddings |
| D2 | **All agent code lives in `backend/`** | New `backend/` package with `sentinel/` (domain) and `api/` (HTTP) |
| D3 | **A new `frontend/`**, Next.js 15 App Router + TypeScript strict + Tailwind, bespoke design system on Radix primitives, **built before any feature screen** | [frontend/DESIGN_SYSTEM.md](../../frontend/DESIGN_SYSTEM.md) is a build gate, not documentation written afterwards |
| D4 | **`sentinel/` is refactored into `backend/` as classes**, not wrapped | `PolicyEngine`, `EvidenceLedger`, `ToolRegistry`, `CaseMemoryStore`, `AnswerValidator`. The 40 policy tests and the 34 live tool assertions port over; `elt.json` and the GSQL layer are kept byte-for-byte |

---

## 2. The problem

A fraud analyst at a mid-size card issuer receives an alert. It says a transaction scored 0.90.
Nothing else.

To decide anything, they open six systems. The card's transaction history, to learn what normal
looks like for this cardholder. The device and identity record, if the purchase was online. The
billing region, and whether the card has ever been used there. The closed-case archive, to see
whether this customer has cried wolf before — or been right before. The fraud policy PDF, to find
which rule applies and who has to approve the action it implies. And a case-management tool, to
write it all down.

Three facts make this worse than tedious:

1. **The score is not the answer.** In this dataset, above 0.7 most flagged transactions turn out
   to be legitimate, and some fraud scores near zero. Guide.md states it directly: *"A score is a
   reason to look, never a verdict."*
2. **The evidence that settles it is relational.** Whether a device fingerprint is shared with four
   other compromised cards, whether a billing region went hot this week relative to a matched
   baseline, whether this exact $49.00 charge recurs monthly — none of it is a row lookup. It is a
   traversal.
3. **Both errors are expensive and only one is visible.** Blocking a legitimate customer is a
   silent cost paid in churn; missing fraud is a loud cost paid in write-offs. An analyst under
   time pressure resolves that asymmetry by blocking, which is exactly what the brief marks down:
   *"Half the cases are legitimate. An agent that blocks everything scores badly."*

An LLM alone makes this worse, not better. Asked to judge fraud from raw rows it will pattern-match
to the score, invent plausible transaction IDs, and route `BLOCK_CARD` to `auto` on case 14 at the
worst possible moment.

## 3. Product thesis

**Sentinel separates the three things a fraud decision is made of, and gives each to the mechanism
that is actually good at it.**

- **Facts come from the graph.** Every number in an answer file is the return value of a named GSQL
  query, cited by a `ref` string the reader can re-run.
- **Belief comes from a calibrated ledger.** Fraud probability is the sigmoid of accumulated
  log-odds, each posting a likelihood ratio fitted on 5,565 real closed cases — not a number the
  model asserts.
- **Decisions come from code.** R1–R10, the 14 action identifiers and the auto/L1/L2 routing table
  are a pure function with one unit test per rule. No prompt can route an action wrongly.

The LLM does the three things it is genuinely best at: choosing which question to ask next,
turning computed facts into readable claims, and writing the regulatory narrative. It writes prose.
It never writes an ID, an amount, an action name, a route, or a filing decision.

## 4. Principles

**P1 — No answer field is produced by free-form LLM text.**
The model's entire writing surface is seven strings: `case.summary`, `case.pattern_description`,
`sar.narrative`, the prose half of `sar.reason`, `next_best_actions.what_changed`,
`evidence[].claim` and `evidence_requests[].assumed_response`. Everything else is computed. This is
enforced structurally — the answer assembler takes typed values from the policy engine, the ledger
and the tool results, and takes prose from the LLM through a separate, narrow interface.

**P2 — Every claim carries its query.**
An evidence item without a re-runnable `ref` and the `entity_ids` it rests on is not evidence.

**P3 — Uncertainty is a first-class output.**
`uncertain` is a valid verdict that earns full credit on ambiguous cases. The system is built to
reach it, say so, and escalate — not to force a binary.

**P4 — Absence is evidence.**
The ledger posts `lr_absent` for features that were checked and not found, so the system can argue
*for* legitimacy rather than merely failing to find fraud. This is what protects the roughly half
of the pack that is legitimate.

**P5 — The permission boundary is real code, not a claim.**
Routes are recomputed server-side from the routing table on every read and every execute. An
`L2` action returns a 403 that names the role which can approve it.

**P6 — Memory compounds inside the run.**
The 20 cases are processed in chronological order, each case written back to the graph before the
next begins, so a later case can cite a case Sentinel itself wrote. Demonstrated, not asserted.

**P7 — Honest instrumentation.**
`tool_calls`, `tokens` and `latency_s` are measured. A file reporting `tokens: 0` from an agent run
is a tell, and the current golden fixture has exactly that because it was built by hand.

## 5. Users and their jobs

| User | Job to be done | What they need on screen |
|---|---|---|
| **Fraud analyst** | Decide what to do about this alert, defensibly, in minutes | The evidence with its citations, the probability and how it moved, the recommended actions with routes, and what is still unknown |
| **Team lead (L1)** | Approve or refuse `DECLINE_TRANSACTION` and `BLOCK_CARD` under $2,500 | The approval inbox, the case behind each request, and the exposure that determines the route |
| **Fraud manager (L2)** | Approve `BLOCK_CARD` over $2,500, `BLOCK_ALL_CARDS` and every `FILE_REPORT` | The same, plus the SAR narrative as the regulator will read it |
| **Judge / reviewer** | Decide whether this system is engineered, or demoed | That the numbers are real, the policy is code, the permission boundary bites, and the 20 answer files validate |

## 6. The experience, narrated

**HHG-019 arrives.** A risk-score trigger: transaction `3503878`, $99.92, online, scored **0.90** —
tied for the highest score in the pack.

The analyst opens it. The timeline starts filling: `scope` resolves the alert to its transaction
and pulls the card's baseline. The probability starts at **0.25**, the fitted prior for a
score-triggered alert, not at 0.90 — because in this dataset the score is a reason to look.

`plan` chooses the detectors. `sweep` runs them and each result posts to the ledger in front of the
analyst: the 0.85+ risk band moves the probability up hard (LR 18.8). Then the graph starts pushing
back — the amount sits inside a band the card has used before, the billing region is well
established, the device is not new, no card-testing sequence precedes it.

`recall` retrieves six closed cases on this card and three policy chunks. `assess` names the
pattern; a second pass argues the exonerating side explicitly and posts what it finds.

The sparkline settles well below where the score alone would have put it. `stop_test` says the
evidence is not yet decisive; `request_evidence` asks the cardholder, and the simulator answers
from the graph — with its basis stated, not invented.

`decide` runs the policy engine twice. **Initial**, before the customer answered: `VERIFY_WITH_CUSTOMER`
(auto) under R1, because the case rested on one signal below 0.70. **Final**, after: the action set
changes, and the diff is on screen with `what_changed` explaining it.

The analyst clicks **Execute** on the blocking action. **403.** The panel says: *BLOCK_CARD routes
to L2 at this exposure. Your role may execute `auto` actions only. `fraud_manager` can approve.* The
request lands in the approval inbox. The role switcher flips to fraud manager, the approval is
granted, the action executes, and one audit row appears with the actor, the route, the approval
reference and a `simulated` chip.

The case is written to the graph as `CASE-HHG-019`. Twelve cases later, a case on a connected card
retrieves it.

## 7. Functional requirements

Each requirement has an id, a statement and an acceptance criterion that can be checked.

### 7.1 Investigation

| id | Requirement | Acceptance |
|---|---|---|
| FR-1 | Investigate an alert from any of the three trigger types — `risk_score`, `customer_report`, `analyst_request` | All 20 alerts in `case_pack.csv` run end to end: 11 / 8 / 1 by trigger type |
| FR-2 | Anchor every time window on `Transaction.ts`, never `Alert.opened_at` | Contract test: for each of the 20 alerts, the `as_of` passed to `region_novelty` equals the flagged transaction's `ts`. The alert lag is +1 h to +6 h on all 20 and never 0 |
| FR-3 | Treat `Alert.risk_score == -1` as absent, reading the model score from `Transaction.risk_score` | Unit test: the 9 alerts carrying the `-1` sentinel post the risk band derived from the transaction, not `risk_00_30` |
| FR-4 | Gather evidence across transaction history, device and identity signals, account behaviour, billing region, prior cases and documents | Every answer file has ≥ 2 distinct `evidence[].source` values; the fraud cases have ≥ 6 evidence items |
| FR-5 | Accumulate belief as calibrated log-odds with correlated evidence capped by group | `fraud_probability` equals the ledger's terminal `p`; no single group moves it more than `GROUP_CAP = 1.2` in log-odds |
| FR-6 | Scope the fraud episode deterministically: `affected_txn_ids`, `first_suspicious_txn_id`, `exposure_usd` | `exposure_usd` equals the summed absolute amounts of `affected_txn_ids` within $0.02, checked against the live graph |
| FR-7 | Identify connected cards and device profiles, gated on fingerprint specificity | `ring_expand` is called with `max_device_cards` set; `connected_device_profiles` uses the `DeviceInfo \| OS \| browser \| screen` label form |
| FR-8 | Name the fraud pattern from the 7 permitted values, and describe it when `undocumented` | `pattern ∈` the enum; `undocumented` ⇒ non-empty `pattern_description` |

### 7.2 Policy and decision

| id | Requirement | Acceptance |
|---|---|---|
| FR-9 | Implement R1–R10 as a pure, deterministic function of case state | The 40 ported tests pass, one per rule plus negatives |
| FR-10 | Use only the 14 action identifiers, verbatim | Validator enum check on all 20 files |
| FR-11 | Derive every approval route from the routing table, recomputed and never trusted | `BLOCK_CARD` routes `L1` at exposure $2,500.00 and `L2` at $2,500.01; all other routes fixed |
| FR-12 | Produce **two** recommendation sets — `initial` before requested evidence, `final` after | Both present in all 20 files; deep-equal when `evidence_requests` is empty |
| FR-13 | Decide `sar.file` from the §3a gate (confirmed or strongly suspected) **and** at least one of: exposure > $1,000, shared origin / other customer's fraud, coordinated or undocumented pattern | `sar.file == ("FILE_REPORT" in final)` on every file, in both directions |
| FR-14 | Cite the rule number in every action `reason` and in `sar.reason`, in both branches | Non-empty `reason` on every recommendation; `sar.reason` present when `file` is false |
| FR-15 | Stop when policy §6 is satisfied, and say why | `stop_reason` non-empty and consistent with the terminal probability and independent support |

### 7.3 Evidence requests

| id | Requirement | Acceptance |
|---|---|---|
| FR-16 | Request further evidence only through the three permitted types: `customer_validation`, `step_up_auth`, `analyst_info` | Enum check |
| FR-17 | Simulate the response from graph-derived basis, never from the desired conclusion, and record the assumption | Each `assumed_response` has a recorded `assumption_basis` in the trace naming the queries it rests on |
| FR-18 | Apply at most one evidence round per case | Enforced by the budget guard, not by convention |

### 7.4 Memory

| id | Requirement | Acceptance |
|---|---|---|
| FR-19 | Retrieve similar prior cases by hybrid retrieval — vector similarity **plus** structural graph filters | Retrieval trace records both a `vector` and a `structural` component and the fused ranking |
| FR-20 | Write each finished case into the graph as a `FraudCase` with its edges, during the run | `FraudCase` count reaches 21 after the benchmark; `CASE_INVOLVES`, `CASE_ON_CARD`, `CASE_CONNECTED_TO`, `CITES`, `IMPLICATES`, `FROM_ALERT` populated |
| FR-21 | Run the 20 cases in chronological `opened_at` order so memory compounds within the run | At least one case's `similar_prior_cases` or `CITES_CASE` edge references a Sentinel case written earlier in the same run |
| FR-22 | Embed case summaries and closed-case narratives so later retrieval is semantic, not just structural | `ClosedCase.emb` populated on 5,565 rows; `FraudCase.emb` populated on write-back |

### 7.5 GraphRAG (required component)

| id | Requirement | Acceptance |
|---|---|---|
| FR-23 | Build a `PolicyDoc` corpus: the Fraud Policy chunked by rule, the five patterns, the action and routing tables, and the FinCEN/FFIEC references | `PolicyDoc` count > 0; each chunk carries `source`, `section`, `title`, `text`, `emb` |
| FR-24 | Ground the LLM with retrieved context rather than raw rows | Prompts carry retrieved chunks; the prompt builder has no path that passes an unfiltered tool result to the model |
| FR-25 | Emit `source: "document"` evidence with a document `ref` | At least one `evidence[].source == "document"` on cases where a rule or typology decided something |
| FR-26 | Ground the SAR narrative in the FinCEN narrative standard | The narrative prompt carries the retrieved FinCEN chunk; narratives are 6–12 sentences covering who/what/when/where/how/why |

### 7.6 Interface (required component)

| id | Requirement | Acceptance |
|---|---|---|
| FR-27 | Show the case queue for all 20 alerts with status, trigger, verdict and probability | Queue renders from `/api/cases` including cases with no answer yet |
| FR-28 | Stream a live investigation — steps, tool calls with refs, evidence postings with probability before/after | SSE timeline advances in real time; reconnect replays from `Last-Event-ID` with no gap |
| FR-29 | Render the probability trajectory as a sparkline | Trajectory drawn from the ledger postings, the same array the trace exposes |
| FR-30 | Show the action panel with route badges and the initial → final diff | Diff view renders both sets and `what_changed` |
| FR-31 | Enforce the permission boundary visibly: execute, get 403 with the required route, approve as the right role, watch it execute | The Step 8 exit test passes in the browser |
| FR-32 | Render the case subgraph — alert, card, transactions, device, connected cards | Graph canvas loads from `/api/graph/cases/{case_id}` |
| FR-33 | Show the SAR as filed, and the reason when not filed | SAR tab renders in both branches |
| FR-34 | Show the memory tab: prior closed cases retrieved, Sentinel cases cited, and how each was found | Retrieval provenance visible per hit |
| FR-35 | Run with fixture data and no backend, for demo resilience | `NEXT_PUBLIC_FIXTURE_MODE=1` renders the full case detail from bundled fixtures |

### 7.7 Deliverable production

| id | Requirement | Acceptance |
|---|---|---|
| FR-36 | Produce `cases/HHG-001.json` … `HHG-020.json` in the required format | 20 files, `python -m eval.validate` exits 0 with graph checks on |
| FR-37 | Validate before writing: nothing invalid reaches `cases/` | Invalid runs write to `runs/HHG-0NN.invalid.json` instead |
| FR-38 | Report the benchmark shape: verdict mix, block rate, probability histogram | Benchmark report available in the UI and as a CLI; warns when block rate > 0.50 or SAR rate > 0.20 |
| FR-39 | Optionally self-trigger on alerts beyond the 20 and write them to a separate folder | Output lands in `exploration/`, never in `cases/` |

## 8. Non-functional requirements

| id | Requirement | Target |
|---|---|---|
| NFR-1 | **Determinism of the decision path** — the same case state yields the same actions and routes | Byte-identical policy output across runs; LLM temperature 0 for structured calls |
| NFR-2 | **Per-case latency** | ≤ 90 s at p95 on a warm workspace; graph calls issued concurrently where independent |
| NFR-3 | **Per-case cost** | ≤ $1.50 hard ceiling enforced by the budget guard; target ≤ $0.30 |
| NFR-4 | **Tool budget** | ≤ 25 graph/retrieval calls per case, enforced |
| NFR-5 | **Cold-start tolerance** | The Savanna workspace auto-stops; a cold call takes ~45 s. Every graph path retries with backoff and the UI shows a warming state rather than an error |
| NFR-6 | **Credential hygiene** | `OPENAI_API_KEY` never leaves the backend. The frontend knows exactly one variable, `NEXT_PUBLIC_API_BASE_URL` |
| NFR-7 | **Auditability** | Every action attempt — executed, denied, failed or replayed — writes one audit row with actor, role, route, approval reference and whether it was simulated |
| NFR-8 | **Reproducibility** | Every run's event journal is persisted and replayable, so a demo never depends on a live model call |
| NFR-9 | **Accessibility** | WCAG 2.1 AA contrast on every token pair; full keyboard path through queue, case and approvals; `aria-live` on the streaming timeline; `prefers-reduced-motion` respected |
| NFR-10 | **Type safety** | Python fully type-hinted with Pydantic v2 models at every boundary; TypeScript `strict` with no `any` in `src/lib` |
| NFR-11 | **Test coverage of the decision path** | Every policy rule and every gate has a unit test; the answer contract has a contract test |
| NFR-12 | **Startup time to first useful screen** | A clean clone to a running UI in ≤ 10 minutes, documented as literal commands |

## 9. Out of scope

- Real authentication. Identity is a header; **authorization** is real and tested. Documented as demo-grade in the OpenAPI description and the blog post.
- Real side effects. Every action except `CREATE_CASE` is simulated, as the brief permits, and labelled `simulated` on the wire and in the UI.
- Retraining the bank's risk model. The score is an input.
- Multi-tenancy, deployment beyond local, and horizontal scale.
- Opening the public IEEE-CIS / Kaggle files. Explicit disqualification; the rule is in the repo README and holds.

## 10. Deliverables

| Deliverable | Where it is produced | Required by |
|---|---|---|
| Working agent | `backend/sentinel/agents/` | Brief |
| GitHub repository | this repo | Brief |
| 20 answer files | `cases/HHG-0NN.json` | Brief |
| Cases written to the graph | `FraudCase` vertices + 6 edge types | Brief |
| SARs where policy requires | `sar` block, narrative 6–12 sentences | Brief |
| Initial and final next-best-actions with routes | `next_best_actions` | Brief |
| User interface | `frontend/` | Brief (required component) |
| GraphRAG | `backend/sentinel/rag/` + `PolicyDoc` | Brief (required component) |
| MCP exposure | `.mcp.json` + `tigergraph-mcp` | Brief (required component) |
| 3–5 minute demo video | recorded from a replayed journal | Brief |
| Technical blog post | six required sections | Brief |
| Social post tagging @TigerGraphDB | X or LinkedIn | Brief |

## 11. Scoring map

| Criterion | Weight | What earns it | Where it is built |
|---|---|---|---|
| **Investigation accuracy** | 25 % | Correct pattern naming, correctly scoped episodes, evidence that is real and cited, and — decisively — getting the legitimate half right | FR-1…FR-8, the `lr_absent` mechanism (P4), the adversarial assessment pass |
| **Next best action** | 25 % | Policy-correct actions and routes, genuine handling of uncertainty, an `initial` → `final` that actually moves on evidence | FR-9…FR-18; the policy engine is code with 40 tests; the evidence simulator is graph-grounded |
| **Agentic design and engineering** | 15 % | Architecture, tool use, orchestration, memory, controls, permissions | The step pipeline, `ToolRegistry`, `CaseMemoryStore`, `BudgetGuard`, `RoutePermissionPolicy` |
| **Innovation** | 15 % | Original use of graph, GraphRAG and agentic capability | Hybrid retrieval that is graph-first, the calibrated ledger, the adversarial exoneration pass, ring discovery, autonomous monitoring (FR-39) |
| **Case summary and explainability** | 10 % | Case progression and clarity of summary, evidence, reasoning | The trace as one data structure serving the answer file, the timeline and the sparkline |
| **Demo quality** | 10 % | A clear end-to-end demonstration | The console, the replayable journal, and the permission-boundary beat |

## 12. Success metrics

| Metric | Target | Why this number |
|---|---|---|
| Answer files valid | 20 / 20, `eval.validate` exit 0 with graph checks | The submission gate |
| Block rate across the pack | ≤ 0.50 | Roughly half the pack is legitimate; blocking more than half is the documented failure mode |
| SAR rate across the pack | ≤ 0.20 | Only HHG-010 clears the $1,000 exposure threshold on its flagged amount alone; every other filing needs a real connection condition |
| Verdict mix | no single verdict > 60 % of the pack | A lopsided mix means the prior is wrong, not the prompt |
| Cases with `initial` ≠ `final` | ≥ 6 | The brief asks explicitly to *show* that the recommendation changed |
| Evidence items per fraud case | ≥ 6 | Thin evidence reads as assertion |
| `tokens` reported as 0 | 0 files | Honest instrumentation |
| Memory compounding | ≥ 1 case citing a Sentinel case from the same run | P6, demonstrated |

## 13. Assumptions and open questions

**Assumptions**

1. The Savanna workspace stays available through the build and the demo, with auto-start enabled. The event-journal replay path exists so the video does not depend on it.
2. `OPENAI_API_KEY` has enough quota for ~20 investigations plus ~5,600 embeddings at 256 dimensions (embedding cost is cents; the investigations dominate).
3. Guide.md's worked example is illustrative. Its IDs are internally inconsistent — it is labelled HHG-017 while the real HHG-017 is card `C04570-K1` / txn `3450629` — so only its **shape** is copied.

**Open questions**

| # | Question | Default taken | When to revisit |
|---|---|---|---|
| Q1 | `elt.json` has no `analyst_request` trigger prior, so `Ledger("analyst_request")` silently falls back to 0.5 | Add an explicit prior of 0.5 and state it, rather than inherit a silent default | Before HHG-014 runs |
| Q2 | The ledger's group cap clamps the increment by remaining room, not the group total, so strong contrary evidence arriving second is over-suppressed | Fix to clamp the group total symmetrically; only same-sign capping is covered by the existing tests, so the fix is safe | During the ledger refactor |
| Q3 | Do the 9 `undocumented` closed cases share a signature? | Investigate during calibration review; if they cluster, naming the pattern is scored under R9 | Milestone M8 |
| Q4 | `in_person_elsewhere_same_day` fitted at LR 0.52 — mild evidence *against* fraud, where intuition says impossible travel | Kept as fitted, revisited with per-card velocity normalisation | Milestone M8 |
| Q5 | Should executing `CLOSE_NO_FRAUD` rewrite the stored answer file? | No. The execution is recorded in the operational store and overlaid in the UI; only a re-run rewrites the submission artefact | Before the demo script is fixed |
