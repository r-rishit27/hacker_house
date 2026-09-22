# Sentinel: An Agentic Fraud Investigator on TigerGraph That Knows When Not to Block

## Our build for the TigerGraph Agentic Fraud Investigation challenge at Hacker House Goa: facts from GSQL, belief from a calibrated ledger, decisions from a policy engine no prompt can override

*By **Team CollabUp**: Rishit Rastogi and Subhash Bishnoi*

A fraud analyst gets an alert. It says a transaction scored 0.90, and nothing else.

To decide what to do, they open six systems:

- the card's transaction history, to learn what normal looks like
- the device and identity record, if the purchase was online
- the billing region, and whether the card has ever been used there
- the closed-case archive, to see whether this customer has been right before
- the fraud policy, to find which rule applies and who has to approve the action it implies
- a case-management tool, to write it all down

By the time they're done, the money has often gone.

**Sentinel** is the agent we built to do that investigation. It's live:

- **Console:** [hackerhouse.collabup.co.in](https://hackerhouse.collabup.co.in/)
- **API documentation (Swagger):** [apihacker.collabup.co.in/api/docs](https://apihacker.collabup.co.in/api/docs)
- **Source code:** [github.com/r-rishit27/hacker_house](https://github.com/r-rishit27/hacker_house/tree/sentinel-v2)

This post follows the structure the challenge asks for: the problem, what we built, the architecture, how TigerGraph is used, the agentic capabilities, what we learned, and what we'd improve. We've added a section on how it's deployed.

![The Sentinel console investigating HHG-002: the case queue, the probability trajectory as each piece of evidence posts, the case subgraph, and the next best actions with their approval routes](https://raw.githubusercontent.com/r-rishit27/hacker_house/sentinel-v2/docs/medium/img/ui-console.png)

---

## The problem

The challenge brief puts it plainly. Fraud teams have to gather transaction history, trace money movement, find connected accounts, check policies, assess risk, write up findings and decide what to do. That work is *"slow, fragmented, difficult to scale, and often completes only after the money is already gone."*

The task is to build an agent that:

1. **Investigates** when triggered by a risk score, a customer report or an analyst.
2. **Gathers evidence** from the knowledge graph, transaction history, device and identity signals, account behaviour and prior cases.
3. **Assesses** the fraud pattern and the level of risk.
4. **Creates and progresses a case**, recording every decision and action.
5. **Uses case memory**, retrieving similar past cases and updating memory as cases resolve.
6. **Gathers more evidence** through controlled actions: asking the account owner, step-up authentication, or asking an analyst.
7. **Recommends next actions**, and updates them as evidence arrives.
8. **Operates within policy and permissions.** Some actions need human approval.
9. **Knows when to stop.**
10. **Explains its reasoning.**

The dataset is IEEE-CIS Fraud Detection from Vesta Corporation, repackaged by TigerGraph. It has about 590,000 card transactions from about 13,500 customers over six months, plus device records for online purchases. It also has closed investigations from the first four months, the bank's fraud policy, five known fraud patterns, and twenty benchmark cases from the last two months.

Three facts about this problem shaped everything we built:

1. **The score isn't the answer.** In this data, most transactions scored above 0.7 turn out to be legitimate, and some fraud scores near zero. The dataset guide says it directly: *"A score is a reason to look, never a verdict."* **There is no fraud label.**
2. **The evidence that settles a case is relational.** Is this device fingerprint shared with other compromised cards? Did this billing region go hot this week compared with a matched baseline? Has this exact $49.00 charge recurred every month? None of those is a row lookup. Each one is a traversal.
3. **Both kinds of error cost money, but only one is visible.** Blocking a legitimate customer is a silent cost, paid in churn. Missing fraud is a loud cost, paid in write-offs. An analyst under time pressure blocks, and that's exactly what the brief marks down: *"Half the cases are legitimate. An agent that blocks everything scores badly."*

An LLM on its own makes this worse. Asked to judge fraud from raw rows, it anchors on the score, invents plausible transaction ids, and eventually routes `BLOCK_CARD` to `auto`.

---

## What we built

Sentinel is built on one idea:

**A fraud decision is made of three things, and each one goes to the mechanism that's actually good at it.**

- **Facts come from the graph.** Every number in a case file is the return value of a named GSQL query, cited by a `ref` string anyone can re-run.
- **Belief comes from a calibrated ledger.** The fraud probability is built up from likelihood ratios fitted on the bank's own closed cases. The model doesn't assert it.
- **Decisions come from code.** Policy rules R1–R10, the 14 permitted actions and the approval routing table are a pure function, with a unit test for every rule. No prompt can route an action wrongly.

The language model does the three things it's genuinely best at: choosing which question to ask next, naming the pattern it sees, and writing prose a human will read. **It never writes an id, an amount, an action name, an approval route or a filing decision.**

For each alert, Sentinel produces a case file containing:

- a **verdict** (fraud, legitimate or uncertain) with a calibrated **fraud probability**
- the **affected transactions**, the first suspicious one, and the dollar **exposure**
- every piece of **evidence**, each with a query reference and the ids it rests on
- the **evidence it asked for**, and the response it assumed
- the **next best actions and their approval routes, recorded twice**: before the extra evidence was requested, and after it came back
- a **Suspicious Activity Report** when policy requires one, or the reason it wasn't filed
- the **stop reason**, and measured tool calls, tokens and latency

The case is then **written back into TigerGraph** as a `FraudCase` vertex with its edges, so later investigations can find it.

Seven principles from our product requirements document kept the build honest:

1. **No answer field comes from free-form model text.** The model can write exactly seven strings, and everything else is computed.
2. **Every claim carries its query.** An evidence item without a re-runnable `ref` and the ids it rests on isn't evidence.
3. **Uncertainty is a first-class output.** `uncertain` is a valid verdict, and the system is built to reach it and escalate rather than force a yes or no.
4. **Absence is evidence.** When a feature is checked and not found, that counts too. This lets the system argue *for* legitimacy rather than merely failing to find fraud.
5. **The permission boundary is real code.** Routes are recomputed on the server on every read and every execution.
6. **Memory compounds within the run.** The twenty cases run in chronological order, and each one is written to the graph before the next starts.
7. **Instrumentation is honest.** Tool calls, tokens and latency are measured, not estimated.

---

## The architecture

![Sentinel system architecture: the Data Foundation, Investigation Layer, Decision Intelligence and Case Operations, and the fraud analyst who starts and reviews each investigation](https://raw.githubusercontent.com/r-rishit27/hacker_house/sentinel-v2/docs/medium/img/system_architecture.png)

The system has four zones.

**The data foundation.** The TigerGraph graph (`schema.gsql`) holds every transaction, card, customer, device, region and closed case. The alert pack (`case_pack.csv`) holds the twenty benchmark alerts. The closed-case history is the only place ground truth is written down.

**The investigation layer.** The graph tools send questions to a suite of installed GSQL queries, and a query audit log records every call. That log is what later turns into citations.

**Decision intelligence.** An offline fitter (`fit_elt.py`) learns, from the closed-case history, how much each piece of evidence should move the probability. It writes those likelihoods to an evidence model (`elt.json`). At run time the evidence ledger loads those likelihoods and turns findings into a probability, and the policy engine turns that probability into ordered actions.

**Case operations.** The case answer (for example `HHG-003.json`) collects citations from the audit log, evidence from the ledger and actions from the policy engine. Three things then happen to it:

- the answer validator checks every id against the graph
- case memory upserts the case back into the graph
- the Sentinel console presents it to the analyst

The fraud analyst selects an alert, which starts the investigation, and reviews the case when it's done.

### Containers

![System context: analysts, team leads and fraud managers use Sentinel, which reads and writes TigerGraph, calls OpenAI, ingests the regulatory corpus once and writes the graded case files](https://raw.githubusercontent.com/r-rishit27/hacker_house/sentinel-v2/docs/medium/img/hld-context.png)

![Containers: a Next.js analyst console talks to a FastAPI backend over REST and Server-Sent Events. The backend hosts the orchestrator and the domain, keeps operational state in SQLite, and reaches TigerGraph and OpenAI](https://raw.githubusercontent.com/r-rishit27/hacker_house/sentinel-v2/docs/medium/img/hld-containers.png)

- **The analyst console.** Next.js 15, TypeScript and Tailwind. It has a case queue, a live investigation timeline, the case subgraph, the SAR, the memory view and an approvals drawer.
- **The API.** FastAPI, with class-based controllers. It streams each investigation over Server-Sent Events, enforces the permission boundary, and writes an audit row for every action attempted.
- **The domain.** Pure Python with no web framework: graph repository, tools, evidence ledger, policy engine, GraphRAG, memory, validation and the orchestrator.
- **SQLite.** It holds only operational state: runs, the event journal, approvals, executions and the audit log. It exists because the audit trail and the journal have to survive a restart, while the answer files must stay exactly as the validator expects. Two stores with two lifetimes, and no duplicate copy of the graded output.

### The ten-step investigation

Early on we decided that a single agent with sixteen tools would work but couldn't be scored. It would have no step boundaries, nowhere to enforce a budget, and one prompt responsible for both naming a pattern and choosing an approval route. So an investigation is **ten numbered steps**. **Four of them use a language model, and six are deterministic code:**

1. **scope** (code). Resolve the alert to its transaction, card and customer. Set the starting probability from the trigger type. Anchor every time window on the *transaction's* timestamp, never the alert's: the alert lags by one to six hours on all twenty cases.
2. **plan** (LLM). Order the detectors and say why for each one.
3. **sweep** (code). Run the GSQL tools concurrently, normalise the results, and post each finding to the ledger.
4. **recall** (code). Run hybrid GraphRAG over past cases and policy text.
5. **assess** (LLM). Name the pattern from the seven permitted values and write the evidence claims. Then a *devil's advocate* pass makes the strongest case that the activity is legitimate.
6. **stop_test** (code). Section 6 of the fraud policy: is there enough evidence to act?
7. **request_evidence** (mixed). If not, ask for one piece of evidence. Code picks the response branch from graph facts, and the model writes only the sentence.
8. **decide** (code). The policy engine runs twice, once for the recommendation before the evidence request and once for the one after.
9. **narrate** (LLM). The SAR narrative, grounded in retrieved FinCEN guidance, plus what changed and the stop reason.
10. **write** (code). Assemble, validate, write the case file, and upsert the `FraudCase` vertex into the graph.

![One investigation, end to end: the UI starts a run, the orchestrator scopes, plans, sweeps tools into the ledger, retrieves, assesses, decides, requests evidence if the stop test isn't satisfied, decides again, narrates and writes the case back to TigerGraph, streaming an event for each step](https://raw.githubusercontent.com/r-rishit27/hacker_house/sentinel-v2/docs/medium/img/hld-investigation-sequence.png)

Every step emits events, and that single event stream serves three purposes. It's the console's live timeline and the audit trace. Through the evidence postings it carries, it's also the `evidence[]` array in the answer file. One data structure produces all three.

Some things were **deliberately kept out of the agent**:

- **Routing.** A prompt will eventually emit `auto` for `BLOCK_CARD`, and a quarter of the score is action quality.
- **Exposure.** It decides both the `BLOCK_CARD` route and the SAR threshold, so one hallucinated transaction would flip both.
- **The SAR filing decision.** It's four boolean reads.
- **The stopping test.** It's two comparisons and a count.
- **The simulated response branch.** If the model picks the branch, it picks the conclusion, and the before-and-after story becomes circular.

### The LLM boundary is a type

![The LLM boundary: the model writes seven strings; ids, amounts, exposure, actions, routes, the SAR filing flag, the probability, enums and booleans are computed; both go through the AnswerAssembler and the AnswerValidator into the case file](https://raw.githubusercontent.com/r-rishit27/hacker_house/sentinel-v2/docs/medium/img/hld-llm-boundary.png)

The model's entire writing surface is seven strings:

1. the case summary
2. the pattern description
3. the SAR narrative
4. the prose half of the SAR reason
5. what changed between the two recommendations
6. the evidence claims
7. the assumed customer response

They live in one object, `Narration`, whose fields are all plain strings. The `AnswerAssembler` accepts typed values from the policy engine, the ledger and the tool results, and it accepts prose only through `Narration`. **There's no code path by which a model response becomes an action name.**

Every finished answer then passes two checks:

- the `AnswerValidator` checks the answer contract, as pure code
- a graph identity checker proves every id exists in the graph and re-checks the exposure against the graph to within two cents

An answer that fails is quarantined in `runs/` and never reaches `cases/`.

### The tech stack

![The Sentinel tech stack, top to bottom: a Next.js 15 analyst console on AWS Amplify; a FastAPI API on AWS EC2 behind nginx; the Python agent and domain; the OpenAI models gpt-5.4-mini for reasoning and text-embedding-3-large for embeddings; TigerGraph Savanna 4.2.5 with 26 installed GSQL queries and the TigerGraph MCP server; and the offline data and quality tooling](https://raw.githubusercontent.com/r-rishit27/hacker_house/sentinel-v2/docs/medium/img/tech-stack.png)

Two OpenAI models do all the language work, and each has a narrow job:

- **`gpt-5.4-mini`** is the reasoning model. It runs in four of the ten steps: planning the order of the detectors, naming the pattern and then arguing the case for legitimacy, wording the simulated customer reply, and writing the SAR narrative and case summary. Every call uses structured output at temperature 0 with a fixed seed, and the output is checked against a schema.
- **`text-embedding-3-large`** is the embedding model, truncated to 256 dimensions. It embeds the 5,565 closed-case analyst notes, the 46 policy chunks and each new case summary, so memory can find a case by what it says as well as by how the graph connects it.

Everything else in the stack is chosen to keep the model on that narrow job. The graph answers questions of fact, and deterministic Python turns those facts into a probability and a decision.

In full:

- **Console:** Next.js 15, React 18, TypeScript 5, Tailwind CSS, TanStack Query and Zustand, hosted on AWS Amplify
- **API:** FastAPI, Uvicorn, Pydantic v2, Server-Sent Events and SQLite, behind nginx on AWS EC2
- **Agent and domain:** Python 3.11+, a custom ten-step orchestrator, and `httpx` for concurrent graph calls
- **Language models:** OpenAI `gpt-5.4-mini` for reasoning and `text-embedding-3-large` for embeddings, at 256 dimensions
- **Graph:** TigerGraph Savanna 4.2.5, GSQL, the TigerGraph MCP server and pyTigerGraph
- **Data and quality:** DuckDB, pytest, `mypy --strict` and Playwright

### Architecture decisions

We recorded nine architecture decision records (ADRs). The ones that shaped the system most:

- **A custom class-based orchestrator, not LangGraph or the OpenAI Agents SDK.** The deterministic policy path is what gets scored, and it would have sat outside any framework anyway. Owning the orchestrator gave us control of step numbering, budget enforcement and event order. It cost about 300 lines of retry, cancellation and streaming code.
- **The API depends on an orchestrator interface, never on the agent.** There are three implementations: the live agent, a scripted one that replays a fixture, and a replay one that plays back a recorded run. The frontend never waited for the agent, and the demo never depends on a live model call.
- **Vectors live in TigerGraph; the hot path reads a local cache.** Embeddings are stored on the vertices themselves, and the shortlist is computed over an in-process matrix of about 5.7 MB.
- **SSE, not WebSocket.** Every event is written to the journal before it's published. Reconnecting with `Last-Event-ID` is then an exact replay with no gaps.
- **Routes are recomputed, never echoed.** A client can't downgrade `BLOCK_CARD` to `auto`.
- **One contract, generated into TypeScript.** The 14 action names, 3 routes and 7 enums come from the Python policy module, and the frontend never hand-types them.
- **An async graph repository over `httpx`.** A case makes about 15 graph calls at 0.7–1.1 seconds each. Run one after another, that's 10–16 seconds of pure latency per case, so independent calls overlap.

---

## How TigerGraph is used

TigerGraph isn't a store the agent occasionally queries. It's where the agent's facts, memory and policy all live.

![The graph model: Customer owns Card, Card made Transaction, transactions chain by NEXT and link to DeviceProfile, BillingRegion, EmailDomain and ProductCode; ClosedCase and Alert attach to cards and transactions; the FraudCase that Sentinel writes links to all of them and to the PolicyDoc rule it applied](https://raw.githubusercontent.com/r-rishit27/hacker_house/sentinel-v2/docs/medium/img/hld-graph-model.png)

### The schema

The graph has 12 vertex types and 20 edge types, each edge with a materialised reverse so traversal direction is free. It holds:

- 590,742 transactions
- 14,317 cards and 13,553 customers
- 9,704 device profiles and 332 billing regions
- 5,565 closed cases
- 20 alerts
- 46 policy chunks

Three facts about this data drove design decisions:

- **A `NEXT` edge chains each card's transactions in time.** There are 576,425 of them, so "how long since the last charge on this card?" is one hop rather than a sort.
- **A `DeviceProfile` is a fingerprint class, not a device.** Just 116 profiles carry 24,653 of the card links, and the largest spans 842 cards. Every device-based connection is therefore gated on how many cards a profile spans. Without that gate, the shared-origin rule (R6) invents a fraud ring on nearly any card ever used online.
- **`APPLIED_RULE` links a case to the policy chunk that decided it.** That turns "which rule decided this?" into a graph question.

### GSQL as the agent's vocabulary

The agent's entire view of the graph is **26 installed GSQL queries**, and 19 of them are exposed to the agent as typed tools with JSON schemas. The rest serve the console, and are withheld on purpose: a tool the agent can call is a tool it can call on the wrong case. There's no text-to-query layer, and the arguments are built from measured values, never by the model.

**The queries return facts, not verdicts.** Rule thresholds such as R5's "three or more small authorisations" and R1's 0.70 live in the policy engine, where they're unit-tested. Some examples of what the queries answer:

- `recurring_charge_probe`: has this same amount and product recurred over months?
- `region_novelty`: is this billing region new *as of the alert*?
- `device_neighbors`: which other cards share this profile, and how many cards does the profile span?
- `txn_sequence_context`: how many seconds since the previous transaction on this card, along the `NEXT` edge?
- `ring_expand_2hop`: which cards connect through *specific* shared devices, two hops out?

### Hybrid GraphRAG

The brief asks for GraphRAG that passes relevant context to the LLM rather than raw data. Pure vector search over a graph database is a weak retriever, so Sentinel runs **two independent rankings and fuses them**:

- **Vector:** cosine similarity over 5,611 embeddings stored on the vertices. These are the 5,565 closed-case analyst notes plus the 46 policy chunks. This finds a case that *reads* like the current one.
- **Structural:** a graph ranking over the same card (weight 3), then the same customer or a shared device (2), then the same pattern with exposure within ±50% (1). This finds a case the graph *connects* to the current one.

A cosine and a hop count have no common scale, so the rankings are combined with **reciprocal rank fusion**. Each result scores `1 / (60 + rank)` in each list, with a bonus when it appears in both. Each surviving hit is then expanded back through the graph for its provenance: which edge connected it, which card, which device.

One **hard filter** runs first: `opened_at < as_of`. A case must never retrieve an investigation that hadn't happened yet. The twenty cases run in chronological order and write their own vertices as they go, so without this filter case memory would be a data leak rather than a capability.

The policy corpus was **parsed out of the organisers' guide**, not copied by hand. It has 46 chunks: 10 rules, 9 policy sections, 5 patterns, 16 glossary terms and 6 guidance notes. Every embedding is `text-embedding-3-large` at 256 dimensions, and embedding the whole corpus cost about a cent.

On HHG-003, the retriever returned **five of the six closed cases** a human had found by working the case by hand. It also returned policy rule R7, *"disputed but legitimate"*, as the top policy chunk. Neither ranking gets both on its own.

### Write-back and case memory

Each finished case becomes one `FraudCase` vertex with its edges, written in a single REST++ call:

- `CASE_INVOLVES` and `CASE_ON_CARD`: the transactions and card involved
- `CASE_CONNECTED_TO`: connected cards
- `CITES` and `CITES_CASE`: the bank's closed cases, and Sentinel's own earlier cases
- `IMPLICATES`: device profiles
- `APPLIED_RULE`: the policy chunk that decided it
- `FROM_ALERT`: the alert that started it

The case summary is embedded too, so a later investigation can find it by similarity as well as by structure.

### TigerGraph MCP

The `tigergraph-mcp` server is wired in over stdio and exposes 69 tools. We checked it by calling a query through it and getting back the same 42 prior transactions in one billing region that the hand investigation had counted. The agent's own traffic goes through the typed Python repository, which is where the refs, the query log and the result normalisation live. MCP shows that the same graph is agent-accessible over a standard protocol.

---

## The agentic capabilities

Here's how each capability in the brief maps to what's built.

- **Investigate from a trigger.** There are three trigger types, each with its own fitted starting probability: 0.25 for a risk score, 0.75 for a customer report and 0.50 for an analyst request. So a 0.90 score starts the investigation at 0.25, because in this data a score is a reason to look.
- **Gather and analyse evidence.** A deterministic sweep of GSQL tools covers transaction history, device and identity signals, account behaviour, billing regions and prior cases, and each finding posts to the ledger.
- **Assess the situation.** The model names the pattern from typology text retrieved by GraphRAG, and the ledger prices the evidence.
- **Create and progress a case.** The case is written to the graph during the run, and the event journal records every step, tool call and posting.
- **Use case memory.** Hybrid retrieval runs over the bank's 5,565 closed cases *and* the cases Sentinel wrote earlier in the same run.
- **Gather more evidence.** Sentinel can ask for customer validation, step-up authentication or analyst review: the three things the policy allows without approval. It gets one round per case.
- **Recommend next actions.** The policy engine runs twice, and both recommendations are recorded.
- **Operate within permissions.** Only `auto` actions execute on their own. `L1` and `L2` actions return a 403 that names who can approve them.
- **Decide when to stop.** It applies Section 6 of the policy, as written.
- **Explain.** Every claim carries a re-runnable `ref`, and every rule cites the policy chunk it came from.

### The evidence ledger

The fraud probability is built up in log-odds:

- It starts from the prior for the trigger type.
- Each finding posts a likelihood ratio from a table of **29 features in 11 groups**. The table was fitted on **14,055 confirmed-fraud transactions** against **300,602 card-matched controls**.
- When a feature was checked and *not* found, the ledger posts its "absent" ratio. That's how Sentinel argues for legitimacy.
- Correlated evidence is **capped per group, symmetrically**, at 1.2 in log-odds. "New device", "device never used on this card" and "proxy present" are three phrasings of one observation, and uncapped they would push a single observation past 0.9 on their own.

Each posting records its claim, its query `ref`, its entity ids, its ratio, and the probability before and after. That one record is the answer file's evidence entry, a point on the console's probability trajectory, and a row in the audit trace.

### Knowing when to stop

The stopping test is Section 6 of the policy, written as code:

- stop if the customer has confirmed or denied the transaction
- stop if the probability is at least 0.85 **and** at least two independent evidence groups support it
- stop if the probability is at most 0.15 **and** at least two independent evidence groups support it
- otherwise, ask for more evidence, at most once

"Independent" means distinct evidence groups whose total moved the probability by more than 0.05. That's what the policy's *"at least two independent pieces of evidence"* actually means.

### Gathering more evidence, honestly

The brief supplies no customer replies, so they have to be simulated. The simulator picks its branch **from measured graph facts**, never from the conclusion the agent is leaning towards:

- **Confirmed:** the charge recurs monthly at the same amount, or the region and amount band are well established.
- **Denied:** the cardholder has a track record of correctly reporting fraud, or a novel device, a novel region and a high score all line up.
- **No reply:** neither side reaches its threshold, which is the honest outcome for an ambiguous case.
- **Step-up passed or failed:** passed when the card has used this device before.

Every simulated response records the queries its branch rests on, and the model writes only the human-readable sentence.

### Before and after

HHG-006 shows why the recommendation is recorded twice:

- **Before the request:** `BLOCK_CARD` (L1), `CREATE_CASE`, `ESCALATE_TO_ANALYST` and `FILE_REPORT` (L2).
- **Evidence round:** Sentinel asked for step-up authentication, and the cardholder passed it.
- **After:** `CREATE_CASE` and `ESCALATE_TO_ANALYST`.

The agent talked itself out of blocking the card and filing a report, and the file records both halves and what changed.

### Permissions that bite

![The permission boundary: an analyst asks to execute BLOCK_CARD; the route permission policy denies it; the approval is enqueued and audited; a 403 names the required route and the roles that can approve; a fraud manager approves, the route is re-checked at decision time, the action executes and is audited with its approval reference](https://raw.githubusercontent.com/r-rishit27/hacker_house/sentinel-v2/docs/medium/img/hld-permission-sequence.png)

The routing table is code:

- Thirteen actions have a fixed route.
- `BLOCK_CARD` is the only one whose route depends on exposure: **L1 at or below $2,500.00, L2 from $2,500.01**.
- An unknown action name raises an error rather than defaulting to `auto`.

Tested end to end over HTTP:

```
analyst POSTs BLOCK_CARD   -> 403 forbidden_route, naming team_lead and
                               fraud_manager, carrying the approval it enqueued
same analyst approves it   -> 403 role_insufficient_for_approval
team_lead approves it      -> executed, simulated, audited
```

Every attempt writes one audit row with the actor, role, route, approval reference and whether it was simulated. That includes the denied ones. As the brief permits, every action except `CREATE_CASE` is simulated, and it's labelled that way on the wire and in the console.

### Budgets and controls

A budget guard enforces, per run:

- 25 tool calls
- exactly one evidence round
- a token ceiling, a dollar ceiling of $1.50, and a wall-clock ceiling

A run that exceeds any of these ends as `budget_exceeded`; it never quietly continues. Every model call is structured output at temperature 0 with a fixed seed. If the output doesn't match the schema, the call is retried with the validation error fed back.

---

## Results

On the twenty benchmark cases:

- **20 of 20 answer files are valid**, against both the answer contract and the live graph.
- **Verdicts: 8 fraud, 9 uncertain, 3 legitimate.** No single verdict covers more than 45% of the pack.
- **The block rate is 10%** (2 of 20), against a 50% ceiling.
- **The SAR filing rate is 10%** (2 of 20), against a 20% ceiling.
- **The recommendation moved after the evidence round on 8 of 20 cases** (40%). On three of them (HHG-002, HHG-006 and HHG-015) the actions themselves changed. On the other five, the same actions ended up resting on a different rule.
- **About 17 seconds, 7,000 tokens and 11–15 GSQL calls per case.** The whole pack runs in under six minutes.

![The Monitor view: 20 of 20 valid, 10% block rate, 10% SAR rate, 40% of recommendations moved after evidence, 9 uncertain as the dominant verdict, the probability distribution and every case as graded](https://raw.githubusercontent.com/r-rishit27/hacker_house/sentinel-v2/docs/medium/img/ui-monitor.png)

Engineering:

- 349 unit and integration tests, which need no network and no credentials
- 16 tests against the live graph
- `mypy --strict` clean across 101 source files
- 28 headless-browser smoke checks on the console

**Ring discovery gave an honest negative.** At one hop, which is the hop rule R6 is defined on, no two of the twenty alerts share a specific device profile, so R6 is right not to fire on this pack. At two hops, one fingerprint does carry twelve cards belonging to twelve customers. The bank had already closed seven of them as confirmed fraud, with $73,178 of exposure. The console shows it, and says exactly why it doesn't count as a ring under the policy.

![The ring view: one device fingerprint carrying twelve cards from twelve customers, seven of them already confirmed fraud with $73,178 of exposure, and the explanation of why it is not counted as a ring at the hop the policy defines](https://raw.githubusercontent.com/r-rishit27/hacker_house/sentinel-v2/docs/medium/img/ui-ring.png)

---

## Deployment

The system is deployed on AWS: the API on EC2 behind nginx, and the console on Amplify.

```
  browser ──https──▶ Amplify (Next.js SSR)
     │                  the console; the API base URL is baked in at build
     │
     └────https──▶ EC2 :443 ── nginx ── :8000 uvicorn (1 worker)
                                            │
                                            ├──https──▶ TigerGraph Savanna
                                            └──https──▶ OpenAI
```

- **Console:** [hackerhouse.collabup.co.in](https://hackerhouse.collabup.co.in/)
- **API and Swagger documentation:** [apihacker.collabup.co.in/api/docs](https://apihacker.collabup.co.in/api/docs)
- **Deployment guide and scripts:** [github.com/r-rishit27/hacker_house/tree/sentinel-v2/deploy](https://github.com/r-rishit27/hacker_house/tree/sentinel-v2/deploy)

The API serves 30 endpoints, grouped as follows:

- **Cases:** the queue, one case, its answer file, trace, validation, SAR, memory and actions, and writing it to the graph.
- **Investigations:** start a run, stream its events over SSE, fetch the recorded events, cancel.
- **Actions and approvals:** execute an action, list executions, list approvals, decide one.
- **Graph:** a case's subgraph for the canvas, and the ring analysis.
- **Benchmark and audit:** the pack report, a batch run, and the audit log.
- **System:** `/health` for liveness, `/ready` for whether the graph, the model and the vector index are reachable, and `/meta` for the enums, routing table and thresholds.

A few deployment lessons worth passing on:

- **An HTTPS page can't call an HTTP API.** The browser blocks it as mixed content before the request leaves the tab, so nothing appears in the network panel. The API needs a hostname and a certificate. Certbot issues it during bootstrap, and `<ip>.sslip.io` works when you don't have a domain.
- **`CORS_ORIGINS` has to match the console's origin character for character.** A mismatch looks exactly like the API being down.
- **The API runs exactly one worker, on purpose.** The run registry, the SSE broker and the investigation tasks are all in-process. A second worker would admit a run the first one had refused, and a browser could attach to a broker that isn't the one emitting its events.
- **Nothing may buffer the SSE stream.** The API sends `X-Accel-Buffering: no`, and the nginx site turns off proxy buffering and gzip on the events route. Otherwise the timeline sits empty and then fills all at once when the run ends.
- **Secrets never go in EC2 user-data**, which anything on the box can read from the metadata service. They live in a `0640` environment file that systemd reads. The service refuses to start without its five required settings, and the model ids deliberately have no defaults.
- **`/ready` is legitimately 503 for the first minute.** The Savanna workspace auto-stops when idle and takes about 45 seconds to wake. When it's asleep, the first response is an HTML "Starting workspace" page instead of JSON. The repository detects that page and retries rather than reporting an error.

There are bootstrap scripts for both Ubuntu 24.04 and Amazon Linux 2023, measured on the actual instances. Redeploying is one command that fetches the latest code, import-checks it while the old process is still serving, restarts, and polls `/health`.

---

## What we learned

We found and fixed twenty-five defects, and every one has a regression test. Four of them taught us something beyond this project, because each one looked completely reasonable.

**1. The risk score was counted twice.** At one point 13 of 20 cases came back as fraud, eight of them pushed past 0.85 by the score alone. A score-triggered alert only exists *because* the model scored the transaction high, and the starting probability for that trigger already includes that fact. Posting the score band's full likelihood ratio on top of it counted the same fact again. We re-centred the band on what the alert already implies. A score in the 0.70–0.85 band now adds nothing beyond the starting probability, and only a score above that band adds anything. On a customer report, the model's score is still genuinely new information, so it counts in full.

**2. Four evidence groups held one observation.** "Online", "distance missing", "all match flags true" and "device found" are four consequences of a purchase happening online, and the ledger was treating them as four separate facts. Across the twenty cases they added +1.84 log-odds to every fraud verdict and −1.45 to every legitimate one. A correlated-evidence cap is only as good as its grouping, and the fitting procedure can't choose the grouping for you.

**3. The agent wasn't deterministic, and we couldn't see it.** Four identical runs of one case, at temperature 0 with a fixed seed, returned probabilities of 0.3577, 0.5025, 0.3569 and 0.3569. The planner was free to add detectors to a mandatory core, and it added a different set each time. A likelihood ratio that never posts isn't neutral: it's missing from the sum. Earlier, a planner given a free choice had dropped the five *exonerating* detectors and returned 0.74 instead of 0.36 on identical facts. Now the detector set depends only on the trigger type, and the planner decides only the order. Three consecutive runs agree to four decimal places.

**4. The simulator wrote a reply the customer would never give.** A cardholder who had reported *"I never made this purchase"* was simulated as confirming the charge, because the recurring-charge probe said it looked like theirs. The resulting file recommended opening a case and closing it as no fraud, both at once. Where the facts and the cardholder disagree, the honest record is a *contest*, and a contest is exactly what policy rule R7 exists for.

**All four were found the same way.** Not by reading code, and not by tests: three hundred of them passed throughout. We took the twenty finished answer files, broke each probability down by evidence group, and looked for a column that was identical on every row. The agent was never broken. It was *composed* wrong, and composition errors are invisible from inside any single component.

A few smaller lessons:

- **Asked cold about HHG-003, the model called it card testing,** twice. Its own explanation gave it away: *"the same $49.00 amount has repeated many times."* That's a recurring charge, which R7 says must never be blocked. The pattern definitions now go into the prompt word for word, and the policy path has no model in it at all.
- **One shared cost meter served the whole batch,** so the token count climbed from 7,541 on the first case to 122,847 on the twentieth.
- **"U.S. dollars" was read as two sentences** by a sentence counter, and that one abbreviation cost a whole answer file.
- **`pytest -m live` skipped all sixteen live tests** because the credentials were in `.env`, not in the environment. At a glance, "16 skipped" reads exactly like "16 passed".

---

## What we'd improve with more time

- **Refit on alerted transactions.** Every ratio compares fraud against random card-matched controls, but we score *alerted* transactions. Re-centring the score fixed the worst instance of that mismatch. The general fix needs alerted legitimate transactions, which this dataset doesn't contain.
- **Learn the evidence groups instead of declaring them.** A correlation matrix over the 29 features would have found the online split in a minute, and would find the next one.
- **Give the devil's advocate its own queries,** so it's an adversary rather than a reviewer arguing from the same findings.
- **Close the loop on outcomes.** Cases are written back with their verdict, but nothing yet reads back whether a later case agreed with them.
- **Move to native vector search in TigerGraph.** Embeddings live on the vertices today, but the similarity search runs in the API process. That's because native vector attributes needed a schema migration on a live 590,742-vertex graph during a hackathon.
- **Add real authentication.** Authorization is real and tested, but identity is a request header. That's fine for a demo and not for production.
- **Try a second dataset.** Every number here is one bank, six months and one model. The interesting question is which of the four errors are general.

---

## Try it

- **Live console:** [hackerhouse.collabup.co.in](https://hackerhouse.collabup.co.in/). Press **Run demo** to walk three real cases, or switch the role between analyst, team lead and fraud manager to see the approval boundary.
- **API documentation:** [apihacker.collabup.co.in/api/docs](https://apihacker.collabup.co.in/api/docs)
- **Source code, design documents and all twenty answer files:** [github.com/r-rishit27/hacker_house](https://github.com/r-rishit27/hacker_house/tree/sentinel-v2)
- **Deployment guide:** [github.com/r-rishit27/hacker_house/tree/sentinel-v2/deploy](https://github.com/r-rishit27/hacker_house/tree/sentinel-v2/deploy)

---

## Team CollabUp

Sentinel was built by **Team CollabUp** for the TigerGraph Agentic Fraud Investigation challenge at Hacker House Goa.

### Collaborators

- **Rishit Rastogi** ([@r-rishit27](https://github.com/r-rishit27))
- **Subhash Bishnoi**

*Built on TigerGraph Savanna with GSQL, the TigerGraph MCP server and GraphRAG, for the TigerGraph Agentic Fraud Investigation challenge at Hacker House Goa.*
