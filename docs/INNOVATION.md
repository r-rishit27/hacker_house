# Innovation claim

## The idea

**Detectors for fraud the policy does not name, which only fire when the card's own
history fails to explain what they see — and a held-out harness that has to agree
before any rule ships.**

Each detector asks a question about *other* cards through the graph: did a device
profile get adopted by many cards at once behind a proxy; did many cards start
paying one recipient this month; did the same burst of just-under-threshold charges
run on other cards. Each look-alike test turns one sentence from the bank's own
cleared-case notes into code ("device marked New, but amount and product fit the
cardholder"). Neither is allowed into the decision path until
`eval/overfit_check.py` shows it does not hurt innocent customers on cases it was
never tuned on.

## Where it changes the outcome

| | Round 1 submission | with the idea |
|---|---|---|
| Round 1's 20 cases, real outcomes | 6 correct · 4 innocent customers blocked | **17 correct · 0 blocked** |
| HHG-006 (structuring, 12 cards) | uncertain, wrong pattern | **fraud, undocumented, 11 connected cards, report filed** |
| HHG-014 (device ring, `D2c2006f554c8`) | uncertain, wrong pattern | **fraud, undocumented, 19 linked cards, report filed** |

The two hidden patterns were the highest-weight cases of Round 1; both now resolve
correctly, from the graph, with the linked cards named.

## Measured on data the rules never saw

- **January planted activity:** sub-$6 charges caught on 18/18 cards; the
  one-recipient ring (`sc.rr.com`) on 12/12; card testing 1/1; escalation 7/8.
  Every card in the look-alike groups (weekly subscriptions, card-present spending,
  usual amounts) stays legitimate: 21/21.
- **Held-out cleared alerts** (the bank's own innocent customers): 2.0% called fraud
  by the rules (n=300).
- **Held-out confirmed frauds:** 299/300 kept as fraud.

## The harness changed the design — the ablation

The harness is not decoration; it overturned three things we had built:

1. A recipient detector that called 43 of 234 July innocents fraud, because its
   "previous six months" ran off the start of the data. Fixed with a coverage guard.
2. A decision model allowed to block a single charge: 10% of held-out innocents
   called fraud. Now it needs a strong corroborator and a multi-charge episode —
   which costs one in-sample case (HHG-019) and is the right trade under Round 2's
   penalty.
3. A logistic look-alike model with cross-validated AUC 0.92 that had learned "new
   device ⇒ innocent" and failed on the held-out twenty. Rejected.

Ablation on the Round 1 replay (each rule switched off): the R1 weak-signal rule is
worth 4 cases and generalises to held-out innocents; R7 is worth one (HHG-004) and
is flagged as the rule most likely to be overfitted.
