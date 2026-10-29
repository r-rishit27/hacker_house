# Round 2 checklist — every requirement in the brief, and where it is met

Brief: https://hhgoa-tg-round2.vercel.app/ · ✅ done and verified · ➖ partial · ⏳ waits on the organisers

## What you submit

| requirement | status | evidence |
|---|---|---|
| Frozen code at a commit hash | ✅ | latest commit on `sentinel-v2` |
| Answer files for all 18 cases, from the frozen code | ⏳ | pack not released; `cd backend && python -m sentinel.typologies.run --pack ../case_pack_final.csv --out ../cases` |
| One command for a single case from a clean clone, setup written down | ✅ | [ROUND2_RUN.md](ROUND2_RUN.md) §1–2; clean clone run verified |
| One-page resilience statement | ✅ | [RESILIENCE.md](RESILIENCE.md), 571 words |
| Innovation claim: one idea and the measurement where it changes the outcome | ✅ | [INNOVATION.md](INNOVATION.md) |
| Report per-case time and tokens honestly | ✅ | `run_log.csv`: latency, graph queries, model tokens, embedding tokens; `tokens` = all model tokens |
| Declare every external model or service and what data leaves | ✅ | RESILIENCE.md, "What leaves the bank's boundary" |
| Use only the data provided; no IEEE-CIS / Kaggle files | ✅ | none downloaded or referenced |
| Every id in an answer exists | ✅ | `python -m eval.self_grade`: 0 hard findings on 20 answers |

## Grader checks (re-implemented in `eval/self_grade.py`; the organisers' grade.py was not in our folder)

| check | status |
|---|---|
| hard: required fields, valid values, internal consistency | ✅ 0 findings |
| hard: ids exist, exposure = sum of affected, first suspicious is earliest | ✅ 0 findings |
| policy: approval routes, R1, R8, R9, R10, §3a, tip-off | ✅ 0 findings |

## Banking guidelines

| guideline | status | evidence |
|---|---|---|
| Do not tip off | ✅ | self-grade tip-off check; evidence replies never mention a report |
| Approvals enforced server-side; a role in the request is not enforcement | ✅ | signed sessions, `api_v2/rbac.py`, `tests/unit/test_portal.py`, `test_auth.py` |
| Fail closed | ✅ | graph down → exit 2, nothing written; model failure → uncertain + escalate; malformed alert → rejected |
| Least privilege for the model | ✅ | model returns five probabilities; no query or write access |
| Protect data: redact in logs, commit no credentials, say what goes out | ✅ | ledger masks e-mail, phone, card numbers; secret scan on every commit |
| Audit trail: every tool call and decision, replayable; approvals not editable | ✅ | hash-chained ledger, `verify`, regulator view recomputes in browser |
| Honest probabilities; escalate when uncertain and exposed; BLOCK_ALL_CARDS only under R10 | ✅ | policy engine; uncertain capped at 0.69; self-grade R8/R10 |

## Drills

| drill | status | evidence |
|---|---|---|
| A dependency failing mid-run | ✅ | DNS failure mid-case and workspace auto-stop both failed closed, logged |
| A model call that times out or returns garbage | ✅ | `test_decisions_backend.py`, `test_adjudicator.py` |
| Hostile or invalid input | ✅ | `eval/attacks.py`; malformed-alert drill (5 kinds), `test_audit.py` |
| Approval attempted by the wrong role | ✅ | analyst → L1 403, team lead → L2 403, regulator → anything 403; each recorded |
| The same case run twice | ✅ | identical verdicts and model scores on repeats; write-back is an upsert |
| No invented answer, no false "saved" | ✅ | `written_to_graph` only after matching read-back |

## Scoring criteria

| criterion | status | where |
|---|---|---|
| Innovation (25) | ✅ | INNOVATION.md; ablation in `eval/overfit_check.py` |
| Harness and critical situations (20) | ✅ | drills above |
| Answer quality (15) | ✅ / ⏳ | Round 1 live: 16 correct, 0 innocents blocked; hidden pack pending |
| Evidence and policy behaviour (10) | ✅ | initial vs final actions with the evidence request that changed them |
| TigerGraph: vector search in decisions, fused with traversal | ✅ | `sentinel/typologies/vector.py` |
| TigerGraph: a graph algorithm that affects output | ✅ | `ty_proxy_components`: connected components (label propagation) over the anonymous-proxy card–device graph, run in TigerGraph; names a ring's cards across devices in the device-ring and ring-device-returns detectors, which flows into connected cards, containment and the report. 100% precision against the bank's labelled rings (CC-2649, CC-5579) |
| TigerGraph: case write-back and read-back | ✅ | `writeback.py`; 20/20 read back |
| TigerGraph: restricted MCP tools | ➖ | Round 1 MCP setup (`scripts/setup_mcp.py`); the Round 2 layer calls installed queries directly |
| TigerGraph: graph as source of truth, live run | ✅ | default source; `eval/graph_parity.py` 20/20 identical |
| Engineering: tests pass, no secrets, clean clone, auth on approvals | ✅ | 79 tests in the Round 2 suite; the Round 1 suite needs the project venv |
| Demo clarity and honesty: README numbers match the live run | ✅ | README quotes the live graph run (16), and the mirror run (17) separately |

## Challenges taken

| # | challenge | status |
|---|---|---|
| 1 | New typologies | ✅ seven detectors, backtested, incl. a known ring device returning to earlier victims (Jan 2017: 8/8 cards) |
| 4 | Evidence to SAR | ➖ filings with ids per sentence; no per-sentence citation links |
| 6 | Regulator's view | ✅ Examiner Register, walkthrough, in-browser chain check |
| 7 | Containment planner | ✅ beats block-all on every ring tested |
| 9 | Speed and autonomy | ➖ p95 reported (170 s, one large card) |
| 10 | Break the agent | ✅ 12 attacks, defended vs undefended |
| 11 | Data quality detective | ✅ gaps, affected ids, tainted investigations |
| 12 | Schema drift survival | ✅ drift report, approval by role, staging gate |
| 14 | Case management | ✅ portal, statuses, approval chain, who decided what |
| 2, 3, 5, 8, 13 | not taken | — |
