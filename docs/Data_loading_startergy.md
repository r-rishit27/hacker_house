# Loading the dataset into TigerGraph Savanna

Two commands, from a clean checkout:

```bash
pip install pyTigerGraph duckdb python-dotenv pandas
python scripts/prepare_data.py          # raw CSVs  -> data/staging/*.csv   (~10 s)
python scripts/load_to_tigergraph.py    # schema + jobs + data + verify
```

Useful variants:

```bash
python scripts/load_to_tigergraph.py --schema-only
python scripts/load_to_tigergraph.py --data-only
python scripts/load_to_tigergraph.py --only transactions next   # retry one file
python scripts/load_to_tigergraph.py --verify-only
python scripts/load_to_tigergraph.py --drop                     # rebuild, asks for confirmation
```

Connection settings live in `.env` (gitignored):

```
TG_HOST=https://tg-....i.tgcloud.io
TG_SECRET=...
TG_GRAPH=GRAPH_GOA
```

## What the two stages do

**`prepare_data.py`** reads the four raw CSVs with DuckDB (every column as text, so
nothing is silently coerced), derives the fields the raw files only imply, and writes
thirteen narrow staging files. `transactions.csv` goes from 708 MB / 393 columns to
187 MB / 23 columns with all 590,742 rows intact. It finishes with five integrity
checks and exits non-zero if any fail, so a bad projection can never reach the graph.

**`load_to_tigergraph.py`** creates the graph and its loading jobs from
[graph/schema.gsql](../graph/schema.gsql) and
[graph/loading_jobs.gsql](../graph/loading_jobs.gsql), uploads each staging file
(chunking anything over 40 MB), then verifies counts and walks a real exam alert
end to end.

## Derived fields

Three things the answer files need are not columns in the dataset.

**`card_id`** (`C08623-K2`). The README says card IDs are derived from the card
issuer field but does not say how. The obvious reading — a distinct `card1..card6`
tuple per customer, numbered by first appearance — produces the right *set* of IDs
and **assigns 10 of the 20 exam transactions to the wrong card**. The actual rule was
recovered by testing candidate rules against the 14,975 `txn -> card_id` pairs implied
by `closed_cases_history.csv` and `case_pack.csv`: **the `card6` value (credit /
debit / charge card) ranked lexicographically within the customer**, which reproduces
all 14,975 pairs exactly. That yields 14,317 cards over 13,553 customers.

This matters because made-up IDs score zero, and `card_id` appears in nearly every
field of every answer file.

**`device_key`** — `D` + the first 12 hex characters of the md5 of
`"DeviceInfo | OS | browser | screen"`. The label string is stored on the vertex
because it is the exact form answer files must quote in `connected_device_profiles`.
9,704 distinct profiles; the largest spans 842 cards.

**Card baselines** — median amount, p95 amount, home region, product mix, channel
split per card. The agent compares flagged activity against the cardholder's own
history, never against a global threshold, so these are computed once at load time
rather than on every query.

## Column projection

Kept in full: the three transaction columns, `ProductCD`, `card1`–`card6`,
`addr1`–`addr2`, `dist1`–`dist2`, both email domains, all of `C1`–`C14`, `D1`–`D15`
and `M1`–`M9`, the four added columns, and the readable identity fields (`id_15`
device new/found, `id_23` proxy, `id_30`/`id_31`/`id_33` for the device label,
`id_34` match status).

The `C`, `D` and `M` families and a 24-column subset of `V` are stored as ordered,
pipe-joined strings (`c_feats`, `d_feats`, `m_feats`, `v_feats`). The column order
for each is written into the graph as `MetaDoc` vertices (`packed_order.c_feats` and
so on), so nothing downstream has to guess. The remaining V columns stay in the raw
file; they are unnamed engineered features and evidence should say so rather than
pretend to know what `V127` means.

Missing numerics are written as **-1**, not left empty: TigerGraph's loader turns an
empty token into 0, which would be indistinguishable from a real zero. The sentinel
is recorded in `MetaDoc:missing_numeric_sentinel`.

## Three TigerGraph 4.2.5 behaviours worth knowing

1. **`proxy` and `Case` are reserved keywords.** `CREATE VERTEX ... (proxy STRING)`
   fails with a parser error that points at the following comma rather than at the
   word itself. Renamed to `proxy_flag` and `FraudCase`.
2. **The GSQL parser rejects some `//` comments**, particularly any containing a
   quote character. The `.gsql` files keep their comments for humans;
   `read_gsql()` strips them before sending.
3. **`HEADER="true"` is ignored on the online POST path** used by
   `runLoadingJobWithFile`. The header row is parsed as data and, for all-string
   vertex types, inserted as a real vertex. The jobs therefore declare
   `HEADER="false"` and the loader strips the header client-side.

Also worth knowing: ingestion is asynchronous. Vertex counts keep climbing for
several seconds after the last upload returns, which is why verification waits
before counting.

## What you should see

| Type | Count |
|---|---|
| Customer | 13,553 |
| Card | 14,317 |
| Transaction | 590,742 |
| DeviceProfile | 9,704 |
| BillingRegion | 332 |
| EmailDomain | 60 |
| ProductCode | 5 |
| ClosedCase | 5,565 |
| Alert | 20 |
| NEXT edges | 576,425 |
| CC_INVOLVES edges | 14,955 |

Verification also walks `HHG-001` to its flagged transaction, to its card and its
baseline, and asserts the alert's card matches the transaction's card — the check
that caught the `card_id` problem in the first place.
