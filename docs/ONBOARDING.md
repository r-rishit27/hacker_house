# Onboarding a new bank

Sentinel's detectors, decision rules, policy engine, approvals and audit trail
do not know any bank's names. They read a narrow canonical model. A new bank is
onboarded by mapping its own graph schema onto that model, reviewing the
mapping, and approving it. No detector or query is written per bank.

```
bank's GSQL schema ──► propose ──► review ──► build (validate) ──► approve ──► run
   (as received)       (model)    (person)    (code)              (signed)    (same detectors)
```

## The canonical model

[`backend/sentinel/onboarding/canonical.py`](../backend/sentinel/onboarding/canonical.py)

| | |
|---|---|
| Concepts | Person, Account, Transaction, Device, IPAddress, LoginEvent, Beneficiary, Card, Merchant, InvestigationCase |
| Relations | OWNS, INITIATED, PAYS_TO_ACCOUNT, PAYS, FROM_DEVICE, FROM_IP, AT, VIA, HOLDS, LOGGED_IN, LOGIN_FROM_DEVICE, LOGIN_FROM_IP, INVESTIGATED_IN |
| Attributes | Transaction.amount / timestamp / channel, Account.open_date / status / kyc_score, Person.anonymized_ssn_hash / kyc_verified_at, Beneficiary.bank_identifier / name_hash, InvestigationCase.status, IPAddress.anonymizer, account_under_investigation |

A mapping can express what real schemas need:

- **several sources for one concept**: current and savings facilities are both Account; T24 transfers and teller cash are both Transaction;
- **a concept that is only an attribute**: T24 has no device vertex, only `PROTOCOL.TERMINAL_ID`;
- **value filters**: an ISO 20022 `Pty` is a Person only where `IsCstmr = true`, a BIAN directory entry is a Beneficiary where `partyType = "Counterparty"`;
- **multi-hop paths with reverse edges**: BIAN ownership is party → relationship plan → facility; a BIAN payment reaches its device through order → authentication → device;
- **value transforms**: T24 `YYMMDDHHMM` strings, `YYYYMMDD` dates, epoch times, YES/NO flags;
- **reviewed code tables**: which `TRANSACTION_TYPE` codes are online, which `paymentMechanismType` values are internet banking.

## The steps

```bash
cd backend
python -m sentinel.onboarding propose ../new_bank.gsql   # -> onboarding/banks/<Bank>/proposed.json
#   edit onboarding/banks/<Bank>/review.json             # a person's changes, each with a reason
python -m sentinel.onboarding build <Bank>               # proposed + review -> mapping.json, validated against the schema
python -m sentinel.onboarding capabilities <Bank>        # which detectors run here, which are off and why
python -m sentinel.onboarding approve <Bank> --token <team lead or fraud manager token>
python -m sentinel.onboarding dryrun <Bank>              # the unchanged detectors on the bank's sample data
```

1. **Propose.** A model reads the schema's structure only (vertex, edge and attribute names, types, directions), never
   the file's comments. Its answer is checked by [`mapping.validate`](../backend/sentinel/onboarding/mapping.py): every
   vertex, attribute and edge must exist, and every path must connect the vertices mapped to the relation's two
   concepts. Anything that fails goes back to the model with the reasons, at most twice; whatever still fails is
   listed, never silently dropped.
2. **Review.** A person supplies what no schema shows: data values for filters and code tables, and anything the
   proposal missed. Each change is recorded with a reason in `review.json`, and `mapping.json` keeps the list, so an
   examiner can see where a person overrode the proposal.
3. **Approve.** A team lead or fraud manager signs the exact `mapping.json` (its SHA-256) with their signed identity.
   Editing the mapping afterwards voids the approval, and an unapproved mapping is refused at run time
   ([`review.load_for_use`](../backend/sentinel/onboarding/review.py)).
4. **Run.** [`MappedTxnSource`](../backend/sentinel/onboarding/adapter.py) turns any mapped graph into the transaction
   rows the detectors already read. It needs four calls from the store (get a vertex, follow an edge, scan a type,
   read an attribute), so on TigerGraph it uses the built-in REST endpoints: nothing is installed per bank.

## Capabilities: what a bank cannot supply is switched off, not guessed

A detector runs only if everything it reads is mapped
([`capabilities.py`](../backend/sentinel/onboarding/capabilities.py)). A payments bank with no devices has the
device-ring detectors **off**, with "missing: Device, FROM_DEVICE" stated, so nobody reads "no ring found" as "no
ring".

## Measured on three test banks

Three schemas built to test exactly this, in [`onboarding/banks/`](../onboarding/banks/): a payments bank built from
**ISO 20022** messages, a retail bank organised by **BIAN** service domains, and a **Temenos T24** core-banking
system loaded as-is. Each file's header records the expected mapping; the proposer never sees it, and
[`truth.py`](../backend/sentinel/onboarding/truth.py) scores proposals against it item by item (79 items in all).

**The proposer alone** (gpt-6-luna, schema structure only):

| | ISO 20022 | BIAN | T24 | all |
|---|---|---|---|---|
| agrees whether the bank has the item | 24/24 | 31/31 | 21/24 | 76/79 |
| status matches exactly (exact / approximate / unmapped) | 20 | 22 | 17 | 59/79 |
| right source for items it mapped | 15/16 | 31/31 | 19/19 | 65/66 |
| **wrong mappings** | 1 | 0 | 1 | **2** |
| attempts · tokens · seconds | 1 · 8,239 · 44 | 2 · 20,709 · 95 | 1 · 8,099 · 48 | |

It avoided every trap the schemas set: the T24 `STMT_ENTRY` ledger, ISO `PmtInf` batches and `TxInf` returns,
`Ultmt*` parties, the BIAN payment order vs its execution, the bank-issued card called `IssuedDeviceAllocation`, and
the fraud score that is not a case. It found BIAN's three-hop path from a payment to its device and IP.

Honest note: these figures are the second run. Two generic rules were added to the instructions after the first run
on these same banks (follow order/authentication chains to a device; prefer a timestamp with time of day). The first
run scored 72/79 agreement, 61/62 sources, 2 wrong. A new bank is the real test.

**After review** (a person's changes, each with a reason, in `review.json`):

| | ISO 20022 | BIAN | T24 |
|---|---|---|---|
| changes | 1 | 17 | 8 |
| agrees / status / sources | 24/24 · 20 · 16/16 | 31/31 · 27 · 31/31 | 24/24 · 22 · 21/21 |
| wrong mappings | 0 | 0 | 0 |
| detectors that run | 7 of 10 (no devices or IPs) | 10 of 10 | 7 of 10 (no link from a transfer to a terminal) |

Why review is not optional: unreviewed, T24's `DATE_TIME` was read as YYYYMMDDHHMMSS instead of YYMMDDHHMM, so
**every** transaction was dropped; BIAN's `paymentMechanismType` had no code table, so no payment counted as online
and structuring could never fire. Both are data facts no schema shows. That is why an unapproved mapping is refused.

**End to end** ([`fixtures.py`](../backend/sentinel/onboarding/fixtures.py), [`test_onboarding.py`](../backend/tests/unit/test_onboarding.py)):
one fraud story written in each bank's own schema, traps included. Through each reviewed mapping, the unchanged
detectors see exactly the 21 real transactions on the account (no ledger postings, orders, batches or returns), find
the structuring burst and its two sibling accounts in all three banks, find the Tor device ring with exactly its
three other accounts in the BIAN bank, and stay quiet on an innocent account. On the two banks with no device data,
the device detectors are reported off instead of reporting "no ring".

```bash
cd backend && python -m sentinel.onboarding dryrun Bank_BIAN
python -m pytest tests/unit/test_onboarding.py -q
```

## Approved, loaded and investigated live

- **Approved.** All three mappings are signed by rishit.rastogi (team lead) and bound to their SHA-256; the
  receipts verify (`onboarding/banks/<Bank>/approval.json`).
- **Loaded.** Each bank is its own graph in TigerGraph, created from its schema file as received and filled with the
  sample records (`python scripts/load_onboarding_samples.py`; `--check` for counts). GRAPH_GOA is untouched. One
  real finding: T24's `CUSTOMER.TARGET` is a GSQL reserved word, so the loader creates it as `TARGET_` and says so;
  no mapping uses that field.
- **Investigated live.** `python -m sentinel.typologies.run --bank <Bank> --pack ../onboarding/banks/<Bank>/case_pack.csv --out ...`
  reads the bank's own graph through its approved mapping (refused if unapproved, exit 2 if the graph is down) and
  writes a validated answer file and audit chain. There is no write-back: these banks' schemas have no Sentinel case
  vertex, and the ledger records that it was skipped.

| bank (live) | verdict | detectors that fired | graph calls | time |
|---|---|---|---|---|
| ISO 20022 | fraud 0.97, report filed, 3 cards blocked | structuring (A1, A2) | 66 | 81 s |
| BIAN | fraud 0.97, report filed | structuring (CAF-1, CAF-2) and the Tor device ring (CAF-3, CAF-4, CAF-5) | 395 | 179 s |
| T24 | fraud 0.97, report filed | structuring (100100001, 100100002) | 170 | 86 s |

## What is still open

- **Speed.** The adapter makes one REST call per hop (about half a second each from here), so a case takes one to
  three minutes on an onboarded bank against 1.5–17 s on GRAPH_GOA. The fix is to generate one installed GSQL query
  per bank from its approved mapping, the way `ty_devices_batch` replaced per-device reads.
- **The console** still shows GRAPH_GOA; investigating in an onboarded bank is the runner (`--bank`) today.
- **Precedent and vectors** come from GRAPH_GOA's closed cases; an onboarded bank's own cases are listed
  (`INVESTIGATED_IN`) but not embedded.
