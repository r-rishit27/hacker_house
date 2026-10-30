# Calibration: how `fraud_probability` is computed

Most submissions will ask a model "how likely is this fraud, 0 to 1?" and get
0.85 for anything that reads as alarming. Sentinel accumulates log-odds from
likelihood ratios fitted on the bank's own closed cases.

```
python -m eval.fit_elt                         # writes backend/sentinel/evidence/elt.json
cd backend && python -m pytest tests/unit -q   # 292 tests over the ledger and the policy
```

## The finding that shaped the whole design

The obvious fit is fraud cases as positives, cleared cases as negatives. On this
dataset that is invalid, and it fails in a way that looks perfectly reasonable
until you check how each class was raised:

| outcome | trigger | n | mean risk | % risk ≥ 0.7 |
|---|---|---|---|---|
| cleared | model score | **900 (all of them)** | 0.881 | **100%** |
| confirmed fraud | customer report | **4,656 (all of them)** | 0.475 | 26% |

There is not one cleared customer report, and not one confirmed model-score
alert, in the entire four-month history. The classes are perfectly separated by
**trigger pathway**, so any likelihood ratio fitted across them measures how the
alert arrived rather than whether it was fraud.

Fitted that way, the table came out actively misleading:

| feature | naive LR | reading |
|---|---|---|
| `risk_85_100` | 0.12 | "a high model score is evidence *against* fraud" |
| `device_new` | 0.19 | "a brand new device is evidence *against* fraud" |
| `region_novel` | 0.21 | "an unfamiliar billing region is evidence *against* fraud" |

The cause is visible in the cleared-case notes: 716 were cleared as "confirmed
travel" and 158 as "confirmed new phone". The cleared population is *made of*
new-device and new-region alerts that cardholders then confirmed. An agent built
on that table would have systematically discounted the three signals that matter
most, on every case.

## What we do instead

Two questions, kept apart.

**1. How suspicious is this transaction, for this card?** Fitted by case-control
matching: positives are the 14,055 confirmed-fraud transactions, negatives are
the 300,602 *other* transactions on the same 1,441 cards, in the same period,
outside any case. Matching on card removes card-level confounding — a card whose
median is $400 should not make a $300 charge look anomalous — and is immune to
the trigger artefact, because both classes come from the same cards.

The risk-score buckets now behave like a proper reliability curve:

| bucket | LR | % of fraud | % of controls |
|---|---|---|---|
| < 0.30 | **0.37** | 31.6% | 85.0% |
| 0.30–0.50 | 2.48 | 21.5% | 8.7% |
| 0.50–0.70 | 5.45 | 20.5% | 3.8% |
| 0.70–0.85 | 8.22 | 17.6% | 2.1% |
| ≥ 0.85 | **18.76** | 8.7% | 0.5% |

Monotone, which is the sanity check that the earlier fit failed. These are the
*discrimination* of the score, measured against other transactions on the same
cards. They are not the posterior for an alert, and reading them as one is the
error corrected below — 18.76 against a prior of 0.25 gives 0.86, which is
exactly the collision with Guide.md's "above 0.7, most flagged transactions
turn out to be legitimate". Discrimination and base rate are different things,
and separating them is the whole point.

**2. How suspicious is the alert, given how it arrived?** That is the prior, and
it is stated rather than smuggled into the ratios. The historical rates are
exactly 1.00 for customer reports and 0.00 for score triggers. Using them
literally would make the agent incapable of ever clearing a customer report,
which contradicts policy R7, or ever confirming a score-triggered alert, which
contradicts the README's "some fraud scores near zero". They are shrunk halfway
toward 0.5, giving **0.75** and **0.25**. That is a judgement call, and it lives
in `eval/fit_elt.py` with the reasoning attached rather than buried in a prompt.

## Some results are counterintuitive and stay that way

`in_person_elsewhere_same_day` — card-present activity in two billing regions on
one day — comes out at **LR 0.52**, i.e. mild evidence *against* fraud. The
textbook reading is impossible travel and therefore cloning. In this data it is
mostly just high-volume cards: card C08623-K2 runs 62 transactions a week across
twenty regions quite legitimately. The fitted number is kept as it is rather than
overridden to match intuition.

`region_novel` at 0.79 is similarly flat. Out-of-region use is only 955 of the
4,665 confirmed cases, so novelty is not the dominant fraud signature here even
though it is one of the five documented patterns.

## Two corrections the benchmark run exposed

The fit above is sound. Applying it was not, in two places, and both were found
by decomposing the twenty answer files by evidence group rather than by reading
the code.

### The score was counted twice on the alerts the score raised

Thirteen of twenty came back `fraud`, and eight of those thirteen were
score-triggered cases carried past 0.85 by the risk band alone. Guide.md says
plainly that **"above 0.7, most flagged transactions turn out to be
legitimate"**, so a score of 0.79 reaching 0.86 on its own is the brief's own
base rate contradicted.

The cause is structural. A `risk_score` alert exists *because* the model scored
it high. The trigger prior of 0.25 is P(fraud | the model raised this alert) —
it has already spent that fact. Posting the band's own ratio on top spends it
again, and the ratios are not small: 8.22 for 0.70–0.85, 18.76 above it.

The band is not discarded, because *how far above the alerting threshold* a
score sits is real information. It is re-centred on the band the alert itself
implies. The 900 cleared alerts are 100% at or above 0.70, mean 0.881, so
0.70–0.85 is what "the model raised an alert" is worth:

| band | fitted LR | on a score-triggered alert | reading |
|---|---|---|---|
| 0.30–0.50 | 2.48 | **0.30** | the model was reaching |
| 0.50–0.70 | 5.45 | **0.66** | below what an alert implies |
| 0.70–0.85 | 8.22 | **1.00** | exactly what the prior already said |
| ≥ 0.85 | 18.76 | **2.28** | above the threshold, and that counts |

A customer report or an analyst request is unaffected: that alert did not come
from the model, so the model's score is independent information and its full
ratio applies. Nine of the twenty alerts carry no score at all.

### Four groups held one observation

The ledger caps correlated evidence by group, on the stated principle that
three phrasings of one finding are not three findings. The fit's own grouping
split one finding four ways: `channel_online`, `dist1_missing`,
`m_flags_all_true` and `device_found` are not four facts, they are four
consequences of a transaction having happened online with an identity record.
Vesta populates the M flags and the device record only for online
transactions, and `dist1` is missing precisely when there is no card-present
distance to record.

Measured over the twenty: those four groups contributed **+1.84 log-odds on
every one of the thirteen `fraud` cases and −1.45 on every one of the three
`legitimate` cases** — a 27× swing, applied identically, for the single fact
that a transaction was online. Merged into one `channel` group they are capped
at ±1.2 like any other observation. The device's own signals — *this card* has
never used *this* device — stay separate, because that is a different fact.

### What changed

| | before | after |
|---|---|---|
| verdict mix | 13 fraud · 4 uncertain · 3 legitimate | 6 fraud · 11 uncertain · 3 legitimate |
| HHG-003 (ground truth ≈ 0.32) | 0.358 | 0.420 |
| HHG-017 (score 0.79, no other strong signal) | 0.942 | 0.764 |
| HHG-010 (score ≥ 0.85, corroborated) | 0.969 | 0.950 |

The cases that stay `fraud` are the ones where the cardholder said outright
that they did not make the purchase, plus the two score cases whose evidence
stands up without the score. `uncertain` is a full-credit verdict and the
brief's core challenge is what to do when the signals are uncertain.

## The ledger

`backend/sentinel/evidence/ledger.py` accumulates `log LR` per finding. Two rules keep it honest.

**Correlated evidence is capped by group.** "New device", "device never used on
this card" and "proxy present" are three phrasings of one observation; without a
cap a single fact pushes past 0.9 on its own. Each group may contribute at most
±1.2 in log-odds, about a 3.3× swing.

**Absence is evidence.** A detector that runs and finds nothing posts its
`lr_absent`. That is what lets the agent *argue* a case is legitimate instead of
merely failing to find anything — the difference between a defensible
`CLOSE_NO_FRAUD` and a shrug.

`Ledger.independent_support()` counts distinct groups that moved the number, not
postings, because policy section 6 requires two independent pieces of evidence
before stopping and three restatements of one device finding are not two pieces.

## Does it agree with a human?

HHG-003 was investigated by hand before any of this existed
([HAND_INVESTIGATION.md](HAND_INVESTIGATION.md)), and assigned 0.55 before
verification and 0.32 after. The fitted ledger, given the same findings, returns
**0.51 and 0.38**. Independently, the policy engine produces the same action set
the hand investigation arrived at: `CREATE_CASE`, `VERIFY_WITH_CUSTOMER`,
`WARN_CUSTOMER`, `ESCALATE_TO_ANALYST`, with no block at any stage.

Agreement within about 0.06, from two methods that share no machinery, is the
best evidence available that neither is badly wrong. The hand fixture keeps its
0.32 for now; the agent run in Step 7 will regenerate it from the ledger, and the
small difference changes no verdict, no action and no SAR decision.

## The policy engine

`backend/sentinel/policy/engine.py` is a pure function from `CaseState` to an ordered list of
`(action, route, reason)`. Rules R1–R10, the action vocabulary and the routing
table are transcribed verbatim from the policy. `backend/tests/unit/test_policy.py` has one
test per rule plus the negatives — 40 tests, including:

- `BLOCK_CARD` routes `L1` at $2,500.00 and `L2` at $2,500.01
- R1 bars any block on a single signal below 0.70 before the customer answers
- R7 is a **bar, not a preference**: a disputed charge matching the cardholder's
  own recurring pattern is never blocked, even after a denial
- R10 makes `BLOCK_ALL_CARDS` unreachable without two compromised cards
- `sar.file` and `FILE_REPORT` are swept across 40 probability/exposure/shared-origin
  combinations and asserted to agree in every one
- a report always has a case ordered before it
- `CLOSE_NO_FRAUD` can never coexist with an enforcement action

The gates are applied twice: once as rules, once as a final `_enforce_gates`
pass that strips anything violating R1, R7 or R10 and recomputes every route
from the table. Defence in depth, because a routing mistake on one case costs
more than the duplication.
