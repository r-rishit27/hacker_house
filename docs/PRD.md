# PRD — **Sentinel**: An Agentic Fraud Investigator on TigerGraph

> **Superseded.** This describes the v1 build that produced the graph, the tool layer, the
> ledger and the policy engine. The system now being built is specified in
> [docs/system/PRD.md](system/PRD.md), [HLD.md](system/HLD.md) and [LLD.md](system/LLD.md).
> Everything recorded here as fact still holds; what changed is listed in system/PRD.md §1.3.

**Version** 1.0 · **Owner** Rishit Rastogi · **Target** TigerGraph Agentic Fraud Investigation, Hacker House Goa
**Status** Draft for build

---

## 1. The problem, stated honestly

A fraud analyst at a bank gets an alert. To decide anything they must pull the card's six-month history, find the other cards that touched the same device, read the billing-region history, dig up the three closed cases that looked like this in August, re-read the fraud policy to find which action needs a team lead's signature, then write a case file and possibly a regulatory filing. That is forty minutes of work per alert, and the bank generates thousands. By the time the analyst finishes, the money has moved.

The naive fix — "auto-block anything the model scores above 0.7" — is worse than the problem. In this dataset **most high-scoring transactions are legitimate** and some real fraud scores near zero. A bank that blocks on model score alone burns its customers and still misses fraud.

**The real job is not detection. It is defensible decision-making under uncertainty.** The agent must be able to say: here is what I found, here is how confident I am and why, here is what I still do not know, here is the action the policy supports right now, and here is how that action changes if the customer answers.

## 2. What we are building

**Sentinel** is an agentic fraud investigator. Given a trigger — a model score, a customer complaint, or an analyst request — it runs a bounded investigation loop over a TigerGraph knowledge graph, accumulates evidence into a **calibrated probability ledger**, retrieves prior closed cases as memory, decides under a **deterministic policy engine** whether it has enough to act, requests more evidence when it does not, and emits a complete case file, a next-best-action pair (before and after evidence), and a SAR when policy requires one — every claim traceable to a named graph query or document.

### 2.1 Product principles

| # | Principle | Consequence for the build |
|---|---|---|
| P1 | **The graph decides facts, the LLM decides meaning.** | Every number in the output (exposure, txn lists, connected cards, counts) comes from a GSQL query. The LLM never invents an ID or an amount. |
| P2 | **Policy is code, not a prompt.** | Actions and approval routes come from a deterministic rule engine over the policy. The LLM cannot emit an action name the policy does not define, or route `BLOCK_CARD` to `auto`. |
| P3 | **Calibration over confidence.** | `fraud_probability` is computed from an evidence log-odds ledger whose weights are fitted on the 5,565 closed cases — not guessed by a model that has seen the word "suspicious". |
| P4 | **Uncertainty is a first-class answer.** | `uncertain` + `VERIFY_WITH_CUSTOMER` + `ESCALATE_TO_ANALYST` is a winning output on ambiguous cases, and the product is designed to reach it, not to avoid it. |
| P5 | **Every claim carries a receipt.** | Each evidence item names its source, the exact query invocation, and the entity IDs it rests on. An analyst can re-run any line of the case. |
| P6 | **Memory compounds.** | Closed cases seed memory; every case Sentinel closes is written back to the graph with an embedding, so case 20 can retrieve case 3. |

### 2.2 Non-goals

- Not a fraud-scoring ML model. The bank already has one; its output is an *input*.
- Not a real-time authorization system. Sentinel investigates alerts, it does not sit in the payment path.
- Not autonomous execution of high-impact actions. `L1`/`L2` actions are *recommended and queued*, never executed.

## 3. Users and their jobs

| User | Job to be done | What Sentinel gives them |
|---|---|---|
| **Fraud analyst (primary)** | Decide on an alert in minutes with evidence they can defend to a manager | A case file with a probability trajectory, an evidence ledger with receipts, and a pre-drafted action set |
| **Fraud manager (L2)** | Approve or reject blocks and regulatory filings | An approval queue showing exposure, route justification, and the SAR narrative ready to read |
| **Compliance officer** | File SARs that survive regulator scrutiny | A six-to-twelve-sentence narrative structured who/what/when/where/how/why, grounded in FinCEN narrative guidance |
| **Fraud ops lead** | Find rings, not just incidents | Cross-case pattern discovery: shared devices, region clusters, undocumented coordinated abuse |

## 4. The core loop

```
TRIGGER -> SCOPE -> EVIDENCE SWEEP -> LEDGER UPDATE -> STOP TEST
                         ^                                |
                         |                   not enough   |  enough
                         +--- CONTROLLED EVIDENCE REQUEST-+
                              (verify / step-up / analyst) |
                                                           v
                                    POLICY ENGINE -> CASE + SAR + NBA -> WRITE TO GRAPH
```

1. **Trigger** — parse the `case_pack.csv` row; trigger type sets the opening prior and the first query plan.
2. **Scope** — resolve customer → cards → the flagged transaction's neighbourhood. Establish the cardholder's *normal*: amount distribution, product codes, regions, channels, cadence.
3. **Evidence sweep** — run the pattern detectors and the connection expanders (§7.1). Retrieve similar closed cases by vector and structural similarity.
4. **Ledger update** — each finding posts a likelihood ratio to the probability ledger; probability is recomputed after every step and recorded as a trajectory.
5. **Stop test** — policy §6: stop at p ≥ 0.85 or p ≤ 0.15 with two independent evidence items, or when a verification settles it, or when further steps cannot change the action.
6. **Controlled evidence request** — when stuck in the middle band, request customer validation / step-up / analyst info. The response is produced by a grounded simulator (§7.4) and recorded as a stated assumption.
7. **Policy engine** — deterministic mapping from (verdict, probability, exposure, connections, pattern, evidence responses) to ordered actions, routes, and rule citations.
8. **Emit and remember** — write the answer JSON, write a `Case` vertex with its evidence and citations into TigerGraph, embed its summary into the vector store.

## 5. Architecture

```
+------------------------- UI: Analyst Console (Next.js) --------------------------+
|  Case queue | Investigation timeline | Evidence ledger | Probability trajectory   |
|  Graph canvas | Approval inbox (L1/L2) | SAR viewer | Memory: prior cases cited   |
+--------------------------------^-------------------------------------------------+
                                 | SSE stream of agent steps
+--------------------------------+-------------------------------------------------+
|  ORCHESTRATOR (LangGraph)                                                         |
|  nodes: scope -> sweep -> assess -> [request_evidence] -> decide -> write -> explain
|  state: EvidenceLedger, QueryLog, CaseDraft, ProbabilityTrajectory                |
+---------------+------------------+-----------------+------------------------------+
| GRAPH TOOLS   | GRAPHRAG         | POLICY ENGINE   | MEMORY                       |
| (MCP -> GSQL) | retrieve+ground  | deterministic   | write-back + embed           |
| 16 named      | policy, patterns | Python, unit-   | ClosedCase + Case vertices   |
| queries       | regs, prior cases| tested, no LLM  | vector + structural recall   |
+---------------+------------------+-----------------+------------------------------+
|  TigerGraph (Community Edition / Savanna): property graph + vector store          |
|  590,742 Transactions, 144,432 identity records, 5,565 ClosedCases, policy corpus |
+-----------------------------------------------------------------------------------+
```

**Stack:** TigerGraph CE 4.x (Docker) or Savanna · GSQL + graph algorithms (Louvain, WCC, cosine kNN) · TigerGraph MCP for tool exposure · LangGraph orchestration · Claude (Opus 5 for assessment and narrative, Haiku 4.5 for extraction and cheap steps) · FastAPI backend · Next.js + React Flow + Recharts UI.

## 6. Graph model

### 6.1 Vertices

| Vertex | Key | Key attributes |
|---|---|---|
| `Customer` | `customer_id` | n_cards, first_seen, last_seen |
| `Card` | `card_id` (`C01234-K1`) | network (`card4`), type (`card6`), issuer codes, home_region, baseline stats (median amt, p95 amt, top product codes, active_days) |
| `Transaction` | `TransactionID` | ts, amt, ProductCD, channel, risk_score, addr1, addr2, dist1, P/R email, C1–C14, D1–D15, M1–M9, curated V-subset |
| `DeviceProfile` | hash(DeviceInfo, id_30 OS, id_31 browser, id_33 screen) | device_type, `label` (the human-readable composite string used in answer files) |
| `EmailDomain` | domain | role carried on the edge (purchaser / recipient) |
| `BillingRegion` | `addr1` | country (`addr2`), n_cards |
| `ProductCode` | `ProductCD` | channel_class |
| `ClosedCase` | `case_id` | outcome, pattern, exposure, dates, actions_taken, report_filed, notes, **embedding** |
| `Case` | `CASE-2016-NNNN` | status, verdict, probability, pattern, exposure, summary, **embedding**, created_by = `sentinel` |
| `PolicyDoc` | section id | text chunk, **embedding** (fraud policy, the five patterns, FinCEN/FATF/FFIEC excerpts) |

### 6.2 Edges

`Customer -OWNS-> Card` · `Card -MADE-> Transaction` · `Transaction -NEXT-> Transaction` (per card, ordered by ts) · `Transaction -FROM_DEVICE-> DeviceProfile` · `Transaction -PURCHASER_EMAIL / RECIPIENT_EMAIL-> EmailDomain` · `Transaction -BILLED_IN-> BillingRegion` · `Transaction -OF_PRODUCT-> ProductCode` · `ClosedCase -INVOLVES-> Transaction` · `ClosedCase -ON_CARD-> Card` · `ClosedCase -CONNECTED_TO-> Card` · `Case -INVOLVES / ON_CARD / CONNECTED_TO-> …` · `Case -CITES-> ClosedCase` · `Case -APPLIED_RULE-> PolicyDoc` · `Case -IMPLICATES-> DeviceProfile`

`Case -CITES-> ClosedCase` and `Case -IMPLICATES-> DeviceProfile` are what make memory *graph-native*: the next investigation that lands on that device walks one hop to every case that ever implicated it.

### 6.3 Loading decision (this one matters)

`transactions.csv` is 708 MB across 393 columns. Loading all 339 V-columns is hours of pain for no analytic gain. **Load all 590,742 rows but a projected column set**: the three transaction columns, `ProductCD`, `card1`–`card6`, `addr1`–`addr2`, `dist1`–`dist2`, both email domains, all C/D/M columns, our four added columns, and a V-subset (group heads from V1–V20, V95–V137, V279–V321) retained as an opaque `v_features` map so evidence can honestly cite "unnamed model features". Full-fidelity rows stay in a local DuckDB alongside for ad-hoc analysis. **No row sampling** — device and region sharing across the whole population is exactly where the rings live.

## 7. The intelligence layer

### 7.1 Graph tools (the agent's hands)

Each is a named, parameterised GSQL query exposed through MCP. The `ref` field of every evidence item is literally the invocation string.

| # | Tool | Answers |
|---|---|---|
| 1 | `txn_detail(txn_id)` | Everything about the flagged transaction plus its identity record |
| 2 | `card_baseline(card_id)` | The cardholder's normal: amount percentiles, product mix, regions, channel split, cadence |
| 3 | `card_window(card_id, ts, hours)` | Ordered transactions around the alert, via `NEXT` |
| 4 | `card_testing_probe(card_id, ts)` | Three or more online auths under $5 within 60 minutes followed by a larger purchase (R5) |
| 5 | `region_novelty(card_id, addr1)` | Is this region new for the card? Did home activity continue in parallel? Trip vs clone |
| 6 | `device_novelty(txn_id)` | `id_15 = New`, `id_23` proxy state, `id_31`/`id_33` mismatch against card history |
| 7 | `device_neighbors(device_key, days)` | Other cards and customers on the same device profile in a window |
| 8 | `region_cluster(addr1, days)` | Card-count spike in a billing region |
| 9 | `email_cluster(domain, role, days)` | Recipient-email fan-in across cards |
| 10 | `ring_expand(seed, hops)` | WCC / Louvain over the device–email–region projection; returns the component and its cohesion |
| 11 | `recurring_charge_probe(card_id, amt, product)` | Same amount and product roughly monthly → R7, disputed but legitimate |
| 12 | `velocity_probe(card_id, ts)` | Burst detection: two to four online within 48h, amount z-score against baseline |
| 13 | `prior_cases_vector(text, k)` | Vector search over `ClosedCase.embedding` and `Case.embedding` |
| 14 | `prior_cases_structural(features, k)` | kNN on the pattern feature vector; returns outcome mix and typical actions taken |
| 15 | `policy_retrieve(question, k)` | Vector search over policy, patterns, and the regulatory corpus |
| 16 | `write_case(case_json)` | Upsert the `Case` vertex, all its edges, and its embedding |

### 7.2 The probability ledger (our calibration edge)

Most submissions will ask an LLM "how likely is this fraud, 0 to 1?" and get 0.85 for everything that looks scary. We do this instead.

**Offline, once.** From `closed_cases_history.csv`, split the transactions of `confirmed_fraud` cases from those of `cleared` cases and from a matched background sample. For each binary evidence feature *e* — new device, proxy flag, region novel, amount above the card's p95, card-testing sequence present, device shared with another card inside 14 days, recipient-email fan-in, risk-score bucket, M-flag mismatch, D1 reset, and so on — compute the **likelihood ratio** LR(e) = P(e | fraud) / P(e | not fraud). Store it as an **Evidence Likelihood Table** with counts and Wilson intervals, shrinking LRs toward 1 where support is thin.

**Online.** Start at prior odds set by trigger type (`customer_report` opens higher than `risk_score`, fitted from the closed cases), anchored so the *case pack* base rate sits near 0.5 rather than the 84% fraud rate of the closed-case file — that file is enriched, and using its base rate directly is a calibration trap. Each evidence item adds `log LR`. Correlated evidence shares a group cap, so three restatements of "new device" cannot triple-count. Probability is the sigmoid of the summed log-odds, recomputed after every step.

**The `risk_score` is one term, and it is capped.** Fit a reliability curve (score bucket → observed fraud rate among closed cases) and use its calibrated LR, which in the 0.7–0.8 band sits close to 1 — the policy's own warning, made numeric.

This buys three things: a defensible `fraud_probability`, a **probability trajectory** for the UI and for `what_changed`, and per-evidence attribution ("the new-device finding moved this from 0.38 to 0.61").

### 7.3 Policy engine (deterministic, unit-tested)

A pure function: `decide(state) -> (actions[], routes[], rule_citations[], sar_decision)`. Rules R1–R10, the action vocabulary, and the routing table are transcribed from the policy verbatim into code, with a test per rule. It enforces:

- `BLOCK_CARD` route flips `L1` ↔ `L2` at $2,500 exposure. `FILE_REPORT` and `BLOCK_ALL_CARDS` are always `L2`.
- R1 hard gate: single signal and p < 0.70 → no block may be emitted; `VERIFY_WITH_CUSTOMER` or `STEP_UP_AUTH` comes first.
- R10 hard gate: `BLOCK_ALL_CARDS` is unreachable unless two or more of the customer's cards show confirmed fraud or credentials are confirmed compromised.
- SAR gate: confirmed or strongly suspected **and** (exposure > $1,000 **or** shared device / region cluster / another customer's fraud **or** coordinated or undocumented). `sar.file` is derived from the same boolean that puts `FILE_REPORT` in the final list, so the two cannot disagree.
- Actions ordered by what happens first, not by severity.
- Output validation: every action name, route, and pattern value checked against the policy enum; every ID checked to exist in the graph before the file is written. **A file that would carry a hallucinated ID fails the build.**

### 7.4 Grounded evidence simulation

Customer replies are not provided, so we must assume one — but assuming at random wastes the "recommendation changes as evidence arrives" score. Our simulator derives the reply from graph evidence and states the derivation:

- Trigger is `customer_report` → the customer has already denied it; the interesting request is step-up auth or analyst information.
- The flagged charge matches a recurring pattern on the card (same amount, same product, roughly monthly) → assume the customer confirms on reflection → R7/R3 path, no block.
- Card-testing sequence plus a new device → assume denial.
- Genuinely ambiguous (p between 0.35 and 0.65, no corroboration) → assume **no reply within 24 hours** → R4 path. This is the honest assumption and a distinct scoreable branch most teams will skip.

Every assumption lands in `evidence_requests[].assumed_response` with its basis, and `next_best_actions.what_changed` explains the probability movement.

### 7.5 Undocumented pattern discovery

The README documents five patterns and says plainly that not every pattern in the data is documented; R9 rewards naming what you find. Sentinel runs a standing discovery pass over the exam window: Louvain on the device–email–region co-occurrence projection, then flags components that are (a) multi-customer, (b) time-concentrated, (c) carrying above-baseline risk scores or amount anomalies, and (d) matching none of the five pattern signatures. An exam case landing in such a component gets `pattern = undocumented`, a written `pattern_description`, and R9's action triple. Rings discovered outside the 20 cases go to the optional folder for Innovation credit.

## 8. The interface

A three-pane analyst console, built so a judge understands it in fifteen seconds.

**Left — case queue.** The 20 exam cases, each with trigger type, current probability, verdict chip, and status. Sorted by exposure × uncertainty.

**Centre — investigation timeline.** The agent's steps as they stream: each step shows the tool called, the finding, the likelihood ratio it contributed, and the probability before → after. A sparkline of the trajectory runs along the top with the evidence-request point marked. Below it sits the case file: verdict, pattern, exposure, affected transactions, connected cards, and the evidence ledger, where every row links to its query and its entity IDs.

**Right — graph canvas and actions.** A focused subgraph (card, flagged transaction, neighbours, device, region, cited prior cases) with fraud-implicated nodes highlighted. Beneath it, the **action panel**: `initial` and `final` recommendations side by side, routes as badges (`auto` green, `L1` amber, `L2` red), rule citations, and a diff of what changed. `auto` actions carry a live Execute button that calls the mock API; `L1`/`L2` actions carry a disabled button reading "Requires team lead / fraud manager" and drop into the **approval inbox**, where a human approves and watches the case advance.

**SAR tab.** The narrative rendered as a filing, with subjects, amounts, and dates.

**Memory tab.** Which prior cases were retrieved and why they matched — and the demo moment, cases *Sentinel itself* closed earlier in the run being retrieved for a later one.

## 9. Deliverables

| Deliverable | Definition of done |
|---|---|
| `cases/HHG-0NN.json` × 20 | Schema-valid, every ID verified against the graph, `sar.file` consistent with `FILE_REPORT`, all 20 present |
| Cases in the graph | `Case` vertices with edges and embeddings; a live query proves memory retrieval |
| Working agent | `python -m sentinel run --all` reproduces all 20 answers from a clean database |
| UI | Deployed, or one command locally; walks a case end to end live |
| GitHub repo | README with architecture, setup in under ten minutes, schema, GSQL, eval harness |
| Demo video, 3–5 min | Script in §11 |
| Blog post | What we built, architecture, TigerGraph's role, agentic capabilities, learnings, next steps |
| Social post | X or LinkedIn, tag @TigerGraphDB, links the blog |
| Optional folder | Autonomous monitoring of Nov–Dec risk scores, plus rings discovered beyond the 20 |

## 10. How each judging criterion is earned

| Criterion | Weight | Our play |
|---|---|---|
| Investigation accuracy | 25% | Sixteen purpose-built graph detectors, baseline-relative anomaly rather than absolute thresholds, prior-case retrieval as a prior. Explicit design for the "half are legitimate" trap: legitimate verdicts must be *argued*, with the recurring-charge and trip-not-clone probes as the two workhorse exonerating findings |
| Next best action | 25% | Deterministic policy engine, R1–R10 unit-tested, exposure-aware routing, calibrated ledger driving the stop test, and a genuine initial → final delta with grounded assumptions including the no-reply branch |
| Case summary and explainability | 10% | Evidence ledger with receipts, probability trajectory with per-evidence attribution, rule citations, FinCEN-shaped SAR narrative |
| Agentic design and engineering | 15% | LangGraph state machine, MCP tool layer, policy-as-code permission boundary, graph-native memory with write-back, schema validation gate, reproducible eval harness |
| Innovation | 15% | Likelihood-ratio ledger fitted on closed cases; Louvain-based undocumented-pattern discovery; memory that compounds *within* the run; grounded evidence simulator; autonomous monitoring mode |
| Demo quality | 10% | Scripted three-act demo hitting fraud, legitimate-under-pressure, and ring |

## 11. Demo script (3–5 minutes)

- **0:00–0:25** — The problem in one sentence over the console: twenty alerts, half legitimate, the model score useless on its own.
- **0:25–1:35 — Act I, a real fraud (card testing).** Hit Investigate. Steps stream: baseline → window → testing probe fires → device is New and shared with another card → prior case CC-xxxx retrieved. Probability climbs 0.20 → 0.78. Initial actions appear with routes. The agent requests verification; the assumption is shown; probability → 0.89; final actions add `BLOCK_CARD` (L2, exposure over $2,500) and `FILE_REPORT`. Click the L2 action: blocked, moved to the approval inbox. Approve as manager; the case closes. Show the SAR.
- **1:35–2:35 — Act II, the trap.** A 0.90-risk-score case that is legitimate. The recurring-charge probe and the trip-not-clone region check drive probability *down* to 0.09; verdict `legitimate`; actions `CLOSE_NO_FRAUD` and `WARN_CUSTOMER`. "The model said 0.90. The graph said no, and here is why." This is the moment that wins accuracy points.
- **2:35–3:35 — Act III, the ring.** The analyst-request case. `ring_expand` lights up a component of cards on one device profile. The undocumented pattern is named and described. R9 actions follow. Then the memory moment: this case cites a case Sentinel closed four cases ago.
- **3:35–4:15** — Architecture diagram; thirty seconds on the ledger and policy-as-code; the graph in TigerGraph showing `Case` vertices written back.
- **4:15–4:30** — All twenty cases green in the eval harness. Repo link.

## 12. Risks

| Risk | Mitigation |
|---|---|
| The 708 MB load stalls the whole build | Column projection and CSV pre-split; load Customer/Card/Transaction first and get the agent working against a partial graph on day one. DuckDB fallback keeps analysis unblocked if loading drags |
| Savanna auto-stops mid-demo | Develop on Community Edition in Docker; record the demo locally; keep Savanna as the cloud checkbox with auto-start enabled |
| Over-blocking, the scored trap | Legitimate-path probes are built first, not last; the eval harness reports block rate and flags any run over roughly 50% |
| The LLM invents an ID or an action name | Hard validation gate before file write; ID existence checked against the graph; enums enforced |
| Ambitious scope, short clock | §13 is ordered so a complete twenty-case submission exists by the end of Day 3; everything after is upside |
| Vector store setup burns hours | Embeddings computed locally and stored as vertex attributes, with a cosine kNN query as the fallback if TigerGraph vector search misbehaves |

## 13. Milestones

| Day | Ships | Gate |
|---|---|---|
| 1 | Graph loaded, schema live, six core queries, one case investigated **by hand** | A hand-written `HHG-003.json` exists |
| 2 | MCP wired, LangGraph skeleton, evidence ledger and ELT fitted, policy engine with R1–R10 tests | Agent produces a valid file for three cases |
| 3 | All sixteen tools, GraphRAG, memory write-back, evidence simulator | **All twenty files generated and schema-valid** |
| 4 | UI, approval inbox, graph canvas, SAR view | Demo runs end to end |
| 5 | Ring discovery, autonomous monitoring, calibration review of all twenty, video, blog, post | Submitted |

## 14. Open questions

1. Does the exam-window base rate really sit near 0.5? Validate by scoring the Nov–Dec population with our detectors and comparing the flag rate against the closed-case era.
2. Is `addr2 != 87` (non-home country) strong enough to stand alone, or only as a multiplier on region novelty? Fit it on the closed cases before deciding.
3. Do the `undocumented` closed cases share a signature? If they cluster, that cluster is the sixth pattern, and naming it is worth real points.
