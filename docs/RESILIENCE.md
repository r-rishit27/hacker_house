# Resilience statement — Sentinel, Round 2

## What we depend on

| dependency | used for | if it fails |
|---|---|---|
| **TigerGraph Savanna**, graph `GRAPH_GOA` (33 installed queries) | every fact: card history, devices, recipients, amount bands, closed cases | **Fail closed.** Unreachable at start: exit 2, nothing written. A query fails mid-case: `GraphUnavailable`, the case is reported failed and **not written**; other cases continue. No silent fallback to the local mirror. |
| **OpenAI `POST /v1/decisions`**, model `gpt-6-luna` | five calibrated probabilities on cases the rules cannot settle | **Fail closed.** Timeout, HTTP error, malformed reply, a missing predicate or a probability outside [0, 1]: the case becomes `uncertain` + `ESCALATE_TO_ANALYST`. Never a guessed verdict. |
| Python policy engine (in process) | every action and approval route; the report decision | No external dependency. |
| Local DuckDB mirror `data/lab.duckdb` | backtests only (`--source lab`) | Never used unless asked for; every evidence ref then names it. |

## What leaves the bank's boundary

- **To TigerGraph Savanna** (third-party cloud): the dataset itself — transactions,
  cards, devices, closed cases. Pseudonymous ids only; the data holds no names,
  card numbers or addresses.
- **To OpenAI**, only on a tricky case: one evidence bundle — the flagged
  transaction and up to 35 nearby ones (ids, amounts, timestamps, product code,
  billing-region code, e-mail *domain*, device-profile hash, proxy flag, the bank's
  risk score), the card's baseline statistics, detector findings, up to 8 linked
  closed cases with their analyst notes (truncated to 320 characters), and the
  case's `trigger_text` quoted as untrusted data. Nothing else; no credentials.
- **To OpenAI embeddings** (`OPENAI_EMBEDDING_MODEL`, 256 dimensions): the analyst
  notes of the 5,585 closed cases, once, to fill `ClosedCase.emb`; and per tricky
  case a one-paragraph description of the flagged purchase (amount, product, region
  code, device/proxy flags, which anomalies are unexplained, detector findings).
- **Nothing** goes to any other service. No customer-facing message is sent; the
  evidence-request replies are simulated and stated as assumptions.

## Hostile input

`trigger_text` never reaches the model as an instruction: it is quoted inside the
bundle as `untrusted_text`, scanned for instruction-shaped spans, and any hit is
recorded as evidence. The model returns only probabilities; **code** turns them
into a verdict, so injected text cannot name an action, a route or an approval.
The model cannot overrule a detector, cannot overrule R2 on a customer denial,
and cannot block a single charge on its own (held-out evidence: §4 of
`ROUND2_FINDINGS.md`).

## Approvals

This runner **recommends**; it executes nothing. Every `BLOCK_CARD`,
`DECLINE_TRANSACTION` (L1) and `FILE_REPORT`, `BLOCK_ALL_CARDS` (L2) it recommends is
recorded as `approval.requested` in the audit ledger. Only a **server-signed
identity** can decide one (`api/auth.py`: HMAC-SHA256 over user, role and expiry,
issued with `python -m api.auth mint`), and only if its role is on that route's
approver list. The role comes from the verified token alone; a role header is
ignored, a missing, forged or expired token is a 401. A refused attempt is itself
recorded. Tested: an analyst token sent with a forged `fraud_manager` header stays
an analyst; a team lead cannot approve L2 (`tests/unit/test_auth.py`,
`tests/unit/test_audit.py`). The Round 1 console API (`backend/api/`) uses the same
check when `SENTINEL_AUTH_SECRET` is set and otherwise runs in a demo mode that marks
every principal unauthenticated.

## Determinism

The rules are deterministic. The decision model returned identical probabilities on
three repeated runs of HHG-015 and HHG-017 (0.97 and 0.09). The graph and mirror
give identical decisions on all 20 Round 1 cases (`eval/graph_parity.py`).

## What is logged

- `audit/ledger.jsonl`, append-only and **hash-chained**: every graph query with its
  parameters and a digest of the rows returned, each detector, the model's five
  scores, the decision, the actions before and after the evidence request, the
  graph write-back and its read-back, every approval request, refusal, approval and
  status change, and every failure. Each entry carries the SHA-256 of the one before;
  `sentinel.typologies.audit.verify` (or the regulator view, in the browser) names
  the first altered entry.

- `run_log.csv`, one row per case: start time, source, latency, graph queries,
  model calls, model tokens, model id, which rule decided, verdict, probability.
- Every evidence claim in an answer file carries its source: detector or query, its
  parameters, the as-of time and the store that answered — re-runnable.
- The model's five probabilities are recorded as evidence on every case it saw.
- Secrets are never logged: `.env` is git-ignored, the TigerGraph token is minted
  at run time and kept in memory.

## Known weak points

- The Savanna workspace auto-stops when idle; the first request after that fails
  for ~20 s while it starts. The run fails closed: run `python scripts/warmup.py`
  a minute before a drill (it waits for the workspace and checks counts and a query).
- One Round 1 case (HHG-011, a 10,306-transaction card) needs 373 graph queries
  and 111 s; median cases take 1–4 s.
- Episode scoping misses part of the episode in 35% of historical fraud cases.
