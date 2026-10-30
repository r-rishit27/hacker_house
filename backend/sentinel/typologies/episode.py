"""Which transactions make up the fraud episode.

Round 2 answers reported the flagged charge alone on customer reports —
HHG-018 came out at $39.08 against the bank's 14 transactions and $2,372.80.
Exposure decides the §3a $1,000 report trigger and BLOCK_CARD's route, so an
under-scoped episode is a policy error, not just a smaller number.

An episode is the flagged charge plus the charges around it that break the
card's pattern *the same way*: on the same channel, sharing one of the flagged
charge's own specific departures from history: the same device, e-mail or
(card-present) region new to the card. The baseline is the history before the
episode window, so the episode cannot vouch for itself.

Measured by ``eval/episode_check.py`` against the transaction lists of
historical confirmed-fraud cases.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from sentinel.typologies.model import Txn

#: Chosen on 300 historical confirmed-fraud cases (eval/episode_check.py): only the
#: flagged charge's *specific* departures, looking forward 14 days, gave 64.7% exact
#: episodes against 59.0% for the flagged charge alone, over-scoping 1% of cases.
#: Generic marks ("some new device", an unusual product) over-scoped 17.7%.
BEFORE = timedelta(0)
AFTER = timedelta(days=14)


def departures(t: Txn, base: Sequence[Txn]) -> set[str]:
    devices = {b.device_key for b in base if b.device_key}
    emails = {b.p_email for b in base if b.p_email}
    regions = {b.addr1 for b in base if b.addr1}
    out = set()
    if t.device_key and t.device_key not in devices:
        out.add(f"device:{t.device_key}")
    if t.p_email and emails and t.p_email not in emails:
        out.add(f"email:{t.p_email}")
    if not t.online and t.addr1 and regions and t.addr1 not in regions:
        out.add(f"region:{t.addr1}")
    return out


def scope(f: Txn, card_txns: Sequence[Txn], extra: Sequence[int] = ()) -> list[int]:
    """Affected transaction ids for a fraud verdict, earliest first."""
    start = f.ts - BEFORE
    base = [t for t in card_txns if t.ts < start]
    window = [t for t in card_txns if start <= t.ts <= f.ts + AFTER]
    marks = departures(f, base)
    ids = {f.txn_id, *extra}
    if marks:
        for t in window:
            if t.channel == f.channel and departures(t, base) & marks:
                ids.add(t.txn_id)
    by_id = {t.txn_id: t for t in card_txns}
    return sorted((i for i in ids if i in by_id), key=lambda i: (by_id[i].ts, i))
