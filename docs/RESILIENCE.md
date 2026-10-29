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

This runner **recommends**; it executes nothing. `BLOCK_CARD`, `DECLINE` (L1) and
`FILE_REPORT`, `BLOCK_ALL_CARDS` (L2) are written with their routes for a human to
approve. In the console API (`backend/api/`) the route check runs server-side and
refuses an under-privileged approval with a 403 — **but the approver's role is read
from an unauthenticated `X-Sentinel-Role` header.** That is a check, not
enforcement: anyone who can reach the API can claim a role. Closing it needs real
authentication (a signed identity the server verifies); it is not done.

## Determinism

The rules are deterministic. The decision model returned identical probabilities on
three repeated runs of HHG-015 and HHG-017 (0.97 and 0.09). The graph and mirror
give identical decisions on all 20 Round 1 cases (`eval/graph_parity.py`).

## What is logged

- `run_log.csv`, one row per case: start time, source, latency, graph queries,
  model calls, model tokens, model id, which rule decided, verdict, probability.
- Every evidence claim in an answer file carries its source: detector or query, its
  parameters, the as-of time and the store that answered — re-runnable.
- The model's five probabilities are recorded as evidence on every case it saw.
- Secrets are never logged: `.env` is git-ignored, the TigerGraph token is minted
  at run time and kept in memory.

## Known weak points

- The Savanna workspace auto-stops when idle; the first request after that fails
  for ~20 s while it starts. The run fails closed, so retry after a warm-up request.
- One Round 1 case (HHG-011, a 10,306-transaction card) needs 373 graph queries
  and 111 s; median cases take 1–4 s.
- Episode scoping misses part of the episode in 35% of historical fraud cases.
