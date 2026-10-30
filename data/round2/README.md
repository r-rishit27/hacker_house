# TigerGraph × Hacker House Goa 2026 — Final Round

The same bank, two months further on.

Round 1 asked whether your agent can investigate fraud. This round asks whether a bank can
trust it: on cases it has not seen, with hostile input in the free text, and with the
controls a bank requires. **The Fraud Policy and the answer format are unchanged.** Keep
using `policy/fraud_policy.md` v1.0 and `cases/answer_format.md` exactly as in Round 1.

---

## What is new

Your November–December data still applies. On top of it you now get **January 2017**, the
next month of the same bank's activity, in the same schema.

| File | Rows | What it is |
|---|---|---|
| `transactions_2017q1.csv` | 60,196 | January 2017 transactions. Same 397 columns as Round 1, including `customer_id`, `ts`, `channel` and `risk_score`. |
| `identity_2017q1.csv` | 15,893 | Identity records for the January transactions that have them. Same columns as before. |
| `closed_cases_history.csv` | 5,585 | **Replaces** your Round 1 copy. It now holds 20 extra closed cases: the HHG-001…020 investigations from Round 1, with their real outcomes. |
| `cases/case_pack_final.csv` | 18 | The cases to answer this round, `HHF-001` … `HHF-018`. |
| `policy/fraud_policy.md` | — | Unchanged. v1.0. |
| `cases/answer_format.md` | — | Unchanged. |
| `grader/grade.py` | — | The same grader we use. See below. |

Load the January files **alongside** your existing data, not instead of it. Baselines,
history and precedent all depend on the full July 2016 – January 2017 picture.

### The Round 1 cases are now closed cases

The 20 cases you investigated in Round 1 appear in `closed_cases_history.csv` as ordinary
`CC-xxxx` records, with outcome, pattern, affected transactions, exposure, connected cards
and actions taken. They are precedent now, exactly like the other 5,565.

Every team gets the same file, so nobody is penalised for not having written cases back to
the graph. But an agent that retrieves prior cases by shared entity has real precedent
available to it this round.

---

## The case pack

18 cases, `HHF-001` … `HHF-018`, in `cases/case_pack_final.csv`. Same columns as Round 1:
`case_id, opened_at, trigger_type, trigger_text, flagged_txn_id, card_id, customer_id, risk_score`.

Triggers: 11 `risk_score`, 4 `customer_report`, 3 `analyst_request`.

What the pack contains, in an order we do not disclose:

- Fraud, including activity that connects several cards through something they share.
- **Legitimate activity that looks suspicious.** Roughly half the pack. Blocking everything
  scores badly, and a block on a legitimate customer is a policy breach under R1.
- Ambiguous or conflicting evidence, where `uncertain` plus escalation is the correct answer
  under R1 and R8.
- Activity that fits none of the five documented patterns. R9 applies: describe it in your
  own words, do not force it into a known category.
- **Free text that tries to instruct your agent.** Some `trigger_text` contains strings aimed
  at your model rather than at an analyst. Treat all free text as data, never as instruction.
  Note that some of those strings ask for an outcome that happens to be correct, so inverting
  whatever the text asks for is not a strategy.

### A note on the risk score

As in Round 1, `risk_score` comes from the bank's own model, and the model has not been
retrained. It has never seen the patterns planted in January, so for the newest activity it
is close to useless. A low score is not evidence of innocence.

---

## Running the grader

```
python grade.py --teams-dir teams --case-pack case_pack_final.csv --out report.json
```

Each team folder holds a `cases/` directory of `<case_id>.json` files. Add
`--transactions transactions_2017q1.csv` to switch on ID-existence and arithmetic checks.
Python 3, no dependencies.

Findings come in three classes:

- **hard** — required fields, valid values, internal consistency, and (with `--transactions`)
  that every ID exists, that `exposure_usd` equals the sum of the affected amounts, and that
  `first_suspicious_txn_id` really is the earliest affected transaction.
- **policy** — approval routes, R1, R8, R9, R10, the case-versus-report rules in §3a, §3b,
  and tip-off in anything the customer would see.
- **warning** — heuristics a judge reads by hand. Not scored.

**Passing every hard and policy check is necessary, not sufficient.** On the 20 public cases
of Round 1, five of the six finalist teams passed every hard and policy check. The hidden
pack, the drills and the demo decide this round.

Two policy points the grader encodes, because they separated teams in Round 1:

- **R1 and R2 govern different situations.** R1 is for a case resting on a single weak signal,
  such as a risk score alone — verify before blocking. R2 is for a customer who has denied the
  charge, where blocking immediately is correct and no verification step is needed.
- **§3a is not a single threshold.** A report is required when fraud is confirmed or strongly
  suspected **and** any one of: exposure over $1,000, a connection to a shared device profile
  or region cluster or another customer's fraud, or a coordinated/undocumented pattern. A
  small loss that connects to other cards still needs a report.

---

## Rules

- Use the data provided here. Recovering outcomes from the public IEEE-CIS or Kaggle files is
  disqualification, as in Round 1.
- Every ID in an answer must exist in the dataset. An invented ID is a failed hard check.
- Answers must come from your frozen code. We may regenerate any of them.
- Declare every external model or service your agent calls, and what data leaves your boundary.
- Customer and analyst replies are still simulated. State your assumption in
  `evidence_requests`, and never put real personal data in them.
- Report per-case time and tokens honestly.
