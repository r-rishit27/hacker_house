# Round 2 — what Round 1 got wrong, and what January contains

Working notes. Every claim carries the ids behind it; the queries run against
`data/lab.duckdb`, built by `python scripts/stage_january.py`.

## 1. Round 1, scored against the real outcomes

The replacement `closed_cases_history.csv` holds HHG-001…020 as CC-5566…5585.
Against them our answers score 6/20.

| outcome | cases | what we said | failure |
|---|---|---|---|
| cleared | 004, 010, 015, 020 | `fraud` 0.89–0.99 | **blocked a legitimate customer** |
| cleared | 005, 013, 002 | `uncertain` 0.75–0.81 | leaning on the same signals |
| confirmed out_of_region | 003, 007 | uncertain 0.42 / legitimate 0.08 | missed |
| confirmed undocumented | 006, 014 | uncertain, named `card_not_present_new_device` | missed both rings |
| confirmed ATO | 018 | uncertain 0.45 | missed |

The cleared-case notes give the look-alike archetypes in the bank's own words:

- "Device marked New, but amount and product fit the cardholder" (005, 020)
- "Amount far above the cardholder's average, but product and email match their history" (002, 010, 015)
- "Customer had a closed fraud case earlier this year; this purchase fits their normal pattern" (004)
- "Late-night purchase; the cardholder has prior late-night activity" (013)
- "Anonymous proxy on the identity record; the cardholder's other purchases show the same proxy use" (017)
- "Several card-present purchases in the same billing region over consecutive days: travel" (001, 012)

Every one is a single anomalous signal **explained by the card's own history**.
The ledger priced the anomaly and never asked whether the history explained it.

## 2. The two undocumented patterns of Round 1

**Structuring under an authorisation threshold** (HHG-006, CC-3748/3841/3907/4086/4124):
four online purchases inside ~40 minutes, each $440–$499.99, total ≈ $1,860–$1,935.
HHG-006's eleven connected cards are the eleven other cards that ran the identical
burst between 2016-11-21 and 2016-12-03 (C05851-K1, C10990-K1, C00466-K1, C01890-K1,
C10751-K1, C03633-K1, C12641-K2, C02265-K1, C05423-K1, C05766-K1, C06881-K1).
The link is the *pattern and the window*, not a shared entity.

**Shared device-profile ring** (HHG-014, CC-2649/2971/2985/3035): one device
profile, never before seen on each account, behind an anonymous proxy, used across
many cards. HHG-014: `D2c2006f554c8` on 27 cards.

## 3. January: the planted activity

January ids are shuffled (half of consecutive pairs go backwards in time), except
**3660000–3660355**, which is grouped by card. That block is where January's
scenarios live. Groups:

| ids | cards | shape | reading |
|---|---|---|---|
| 3660000–095 | C13487-K1, C13489…C13542 | card-present W, normal amounts, long history | legitimate look-alikes (C13487-K1 is the HHG-014 victim) |
| 3660096–130 | 12 cards (C05704-K2, C00450-K1, C07925-K1, …) | online C, **new device + anonymous proxy**, 2–3 charges $250–$890 in 1–2 days | CNP new-device fraud; check for a shared origin |
| 3660131–173 | C09026, C09148, C07847, C11663, C08943, C08563, C04096, C03080 | online C, 5–7 charges **escalating to a $1,800 cap** | several are **dormant cards waking** (C09148-K1 last seen 2016-10-01, C08563-K1 2016-10-02) |
| 3660174–260 | 17 cards | **$1–$6 charges spread over weeks** | sub-threshold micro-skimming |
| 3660261–289, 311–320 | 18 cards | online C, **R_emaildomain `sc.rr.com`** | many cards paying one recipient |
| 3660290–298 | C09241-K1, C00488-K1, C09468-K1 | C09468-K1 dormant since 2016-10-01, then $964–$1,274 | dormant wake |
| 3660299–310 | C12223-K1, C12030-K1, C04105-K2 | identical $7.42 / $7.78 / $6.57 **every Wednesday 09:00** | recurring subscription — legitimate look-alike to micro-charges |
| 3660321–345 | 9 cards | online C at the card's usual amount | legitimate look-alikes |
| 3660346–350 | C09582-K1 | $1.53, $3.46 … then $236.28 within an hour | card testing |

`sc.rr.com` as a recipient: 7 cards in six months, 17 cards in January alone.

## 4. The decision layer, and how we checked it is not overfitted

`backend/sentinel/typologies/` — detectors, look-alike explainers, `decide.py`, and the
OpenAI `/v1/decisions` adjudicator (`gpt-6-luna`, five predicates, code turns the
probabilities into a verdict). Order: typology → R2 → explained → R1 weak single →
R7 → model (gated). Reproduce with `python -m eval.overfit_check [--model]`.

| measurement | rules only | rules + model |
|---|---|---|
| Round 1 replay (in-sample), local mirror | 16 correct · 3 uncertain · 1 wrong · 0 harmed | 17 · 2 · 1 · 0 |
| Round 1, live on TigerGraph (model, vector precedent, write-back, audit) | — | **16 · 3 · 1 · 0**, 20/20 written and read back |
| held-out cleared alerts called fraud (innocent harmed) | 2.0% (n=300) | 5.0% (n=80) |
| held-out confirmed frauds kept as fraud (as customer reports) | 299/300 | 79/80 |
| January look-alike groups (card-present, subscriptions, usual amounts) | 21/21 legitimate | 21/21 |
| January fraud groups: micro / recipient / escalation / card testing | 18/18 · 12/12 · 7/8 · 1/1 | same |
| January new device + anonymous proxy | 2/14 fraud | 11/14 fraud |

What the check changed, each found on held-out data, not on the twenty:

- **Data-start artefact.** In July 2016 the recipient detector's "previous six
  months" was empty: 43 of 234 July cleared alerts were called fraud. Every
  detector with a "before" window now requires the data to cover it.
- **Card testing** now follows R5 as written (≥3 small online authorisations in an
  hour, then a larger purchase).
- **The model alone may not block a single charge.** Held out, the bank cleared
  single-charge alerts with a new device behind a proxy (CC-1024, CC-1165) and with
  a device new to the bank (CC-3385) — the same shape as HHG-019, which was fraud.
  A model fraud call on a score alert needs a strong corroborator *and* a
  multi-charge episode. This costs HHG-019 (now uncertain) and is the right trade
  under Round 2's penalty.
- **Ablation** (replay with each rule off): R1-weak-single is worth 4 cases and
  generalises (it clears held-out innocents too); R7 is worth 1 case (HHG-004) and
  never misfired on 300 historical frauds — the rule most likely to be overfitted.
- **Rejected:** a logistic look-alike model fitted on closed cases (CV AUC 0.92, but
  it learned "new device ⇒ innocent" from the cleared-alert notes and failed on the
  held-out twenty); `match_status:0` (as rare in fraud as in controls).

Episode scoping (`episode.py`, `eval/episode_check.py`, 300 historical cases): exact
episodes 64.7% vs 59.0% for the flagged charge alone; §3a $1,000 trigger agrees 93.3%;
over-scoped 1%; still under-scopes 35% (multi-charge account takeovers).

## 5. GRAPH_GOA (Round 2)

Rebuilt on the Savanna workspace from `data/staging_r2` (July 2016 – January 2017):
`python scripts/stage_january.py` then `STAGING_DIR=data/staging_r2 python
scripts/load_to_tigergraph.py`. Verification passes on every vertex and edge type:
650,938 transactions, 636,621 NEXT edges, 5,585 closed cases, 156,314 device edges.
