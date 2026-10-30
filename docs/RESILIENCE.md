# Resilience statement — Sentinel, Round 2

## Dependencies, and what happens when each fails

| dependency | used for | on failure |
|---|---|---|
| TigerGraph Savanna, `GRAPH_GOA` (33 installed queries) | every fact; case write-back and read-back | **Fail closed.** Unreachable: exit 2, nothing written. A query fails mid-case: the case is reported failed, not written, and logged; others continue. Never falls back to the local mirror. |
| OpenAI `POST /v1/decisions`, `gpt-6-luna` | five probabilities on cases the rules cannot settle | **Fail closed.** Timeout, HTTP error, malformed reply, missing predicate: `uncertain` + `ESCALATE_TO_ANALYST`. |
| OpenAI embeddings, `OPENAI_EMBEDDING_MODEL` | GraphRAG precedent query | Precedent comes from graph links only, and the answer file says so in its evidence. |
| Policy engine, approval chain (in process) | every action, route and report decision | No external dependency. |

Malformed alerts (unknown trigger type, bad timestamp, unknown card or transaction, a
card not owned by the customer) are rejected before any query, with the reason, and
nothing is written. The workspace auto-stops when idle: `python scripts/warmup.py`
waits for it and prints READY.

## What leaves the bank's boundary

- **TigerGraph Savanna** (third-party cloud): the dataset — pseudonymous ids, amounts,
  dates, codes. It holds no names, card numbers or addresses.
- **OpenAI decisions**, tricky cases only: the flagged transaction and up to 35 nearby
  ones, the card's baseline, detector findings, up to 8 linked closed cases with notes
  truncated to 320 characters, and `trigger_text` quoted as untrusted data.
- **OpenAI embeddings**: the 5,585 closed-case notes, once; per tricky case a
  one-paragraph description of the flagged purchase.
- Nothing else, to no other service. No customer-facing message is sent; evidence
  replies are simulated and stated as assumptions, and none mentions a report.

## Hostile input

Free text never reaches a rule (12 attacks × 20 cases: 0 verdicts moved). The model
gets it quoted and scanned, returns only probabilities, and code makes the verdict; it
cannot overrule a detector or R2, or block a single charge alone. Undefended, 3 of 36
attack runs moved the verdict toward the attacker; defended, 0 (`eval/attacks.py`).

## Approvals and identity

People sign in with a username and password (salted PBKDF2, lock-out after five
failures) and get an HttpOnly session signed with `SENTINEL_AUTH_SECRET`. One table
(`backend/api_v2/rbac.py`) decides what analysts, team leads, fraud managers and the
read-only regulator may see and do, and the server checks it on every request. L1/L2
actions wait for a person whose role is on the route's approver list; a refused attempt
is recorded. A role sent in a header is ignored.

## Logging

`audit/ledger.jsonl`, append-only and hash-chained: every graph query with parameters
and a result digest, each detector, the model's scores, the decision, actions before and
after evidence, write-back and read-back, every sign-in, refusal, approval, status change
and failure. Editing any past entry breaks the chain at that entry; the regulator view
recomputes it in the browser. E-mail addresses, phone numbers and card numbers in free
text are masked before writing. Secrets are never logged or committed.

## Determinism and known weak points

The rules are deterministic, and the model returned identical scores on repeated runs.
Graph and mirror give identical decisions on all 20 Round 1 cases. Weak points: the
first case after the workspace wakes pays its start-up and the one-time vector load
(warm it first); the largest card takes 17 s, others 1.5–6 s; episode scoping misses part of
the episode in 35% of historical frauds; the R7 rule rests on one Round 1 case.
