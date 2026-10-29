"""Seven detectors. Five are undocumented typologies; one is the documented
card-testing shape; one is the shared-device ring of Round 1.

Every threshold below is a stated number with the reason beside it, and every
detector is backtested by ``eval/backtest_typologies.py`` — what it catches on
the 4,675 confirmed-fraud cases, and what it fires on among transactions that
were never in any case.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from sentinel.typologies.model import Txn, TxnSource, TypologyHit

#: How far back from the as-of time an episode may start.
EPISODE_WINDOW = timedelta(days=35)

#: Authorisation thresholds the structuring detector looks just under.
AUTH_THRESHOLDS = (500.0, 1000.0)
#: "Just under" — within 12% of the threshold. Round 1's bursts sat at 88–99.9%.
STRUCTURING_BAND = 0.88

#: A device on more cards than this is a browser default, not a fingerprint
#: (the same gate rings.py uses; the largest profile spans 842 cards).
SPECIFIC_DEVICE_MAX_CARDS = 40

#: Micro charges: under this amount, on a card whose usual spend is well above it.
MICRO_MAX = 6.0
MICRO_CARD_MIN_MEDIAN = 15.0

#: Dormancy: silence this long before the episode.
DORMANT_GAP = timedelta(days=28)

#: Recipient concentration: an e-mail recipient this many cards newly pay inside a month.
RECIPIENT_MIN_CARDS = 8
#: Domains this common are mail providers, not recipients.
RECIPIENT_COMMON_CARDS = 60


def covered(src: TxnSource, since: datetime) -> bool:
    """Whether the data reaches back to ``since``. A "before" window that runs off
    the start of the data makes everything look new: in the July 2016 backtest the
    recipient detector called 43 of 234 cleared alerts fraud for exactly that reason."""
    start = getattr(src, "data_start", None)
    return start is None or since >= start


def _episode(txns: Sequence[Txn], as_of: datetime) -> list[Txn]:
    return [t for t in txns if as_of - EPISODE_WINDOW <= t.ts < as_of]


def _before(txns: Sequence[Txn], ts: datetime) -> list[Txn]:
    return [t for t in txns if t.ts < ts]


def _median(xs: Sequence[float]) -> float:
    return statistics.median(xs) if xs else 0.0


def _identical(amts: Sequence[float]) -> bool:
    """Organic repeat purchases are the same price; structured ones are not."""
    return len(amts) > 1 and (max(amts) - min(amts)) <= 0.01 * max(amts)


def _ids(txns: Sequence[Txn]) -> tuple[int, ...]:
    return tuple(t.txn_id for t in sorted(txns, key=lambda t: (t.ts, t.txn_id)))


# ---------------------------------------------------------------------------
# 1. Structuring just under an authorisation threshold
# ---------------------------------------------------------------------------


def _bursts(txns: Sequence[Txn], threshold: float) -> list[list[Txn]]:
    lo = threshold * STRUCTURING_BAND
    band = sorted((t for t in txns if t.online and lo <= t.amt < threshold), key=lambda t: t.ts)
    out: list[list[Txn]] = []
    i = 0
    while i < len(band):
        j = i
        while j + 1 < len(band) and band[j + 1].ts - band[i].ts <= timedelta(minutes=60):
            j += 1
        group = band[i : j + 1]
        if len(group) >= 3 and not _identical([t.amt for t in group]):
            out.append(group)
            i = j + 1
        else:
            i += 1
    return out


def structuring(card: str, txns: Sequence[Txn], src: TxnSource, as_of: datetime) -> TypologyHit | None:
    for threshold in AUTH_THRESHOLDS:
        bursts = _bursts(_episode(txns, as_of), threshold)
        if not bursts:
            continue
        burst = bursts[-1]
        # Siblings: other cards running the same burst within three weeks either side,
        # bounded by as_of so the detector never reads the future.
        since = burst[0].ts - timedelta(days=21)
        until = min(as_of, burst[0].ts + timedelta(days=21))
        by_card: dict[str, list[Txn]] = defaultdict(list)
        for t in src.txns_in_band(threshold * STRUCTURING_BAND, threshold, since, until):
            if t.card_id != card:
                by_card[t.card_id].append(t)
        siblings = sorted(c for c, ts in by_card.items() if _bursts(ts, threshold))
        total = round(sum(t.amt for t in burst), 2)
        return TypologyHit(
            key="structuring_under_threshold",
            pattern="undocumented",
            description=(
                f"{len(burst)} online purchases within "
                f"{int((burst[-1].ts - burst[0].ts).total_seconds() // 60)} minutes, each between "
                f"${min(t.amt for t in burst):,.2f} and ${max(t.amt for t in burst):,.2f} — just under a "
                f"${threshold:,.0f} authorisation threshold — totalling ${total:,.2f}"
                + (f"; the same burst ran on {len(siblings)} other cards in the same three weeks" if siblings else "")
                + ". Amounts appear chosen to stay under the threshold."
            ),
            txn_ids=_ids(burst),
            connected_card_ids=tuple(siblings),
            shared=f"burst of charges just under ${threshold:,.0f}",
            strength=1.0 if siblings else 0.8,
            evidence={"threshold": threshold, "total": total, "n_siblings": len(siblings)},
        )
    return None


# ---------------------------------------------------------------------------
# 2. Shared device-profile ring
# ---------------------------------------------------------------------------


def device_ring(card: str, txns: Sequence[Txn], src: TxnSource, as_of: datetime) -> TypologyHit | None:
    """One device profile adopted by many cards at once, mostly behind a proxy.

    The specificity gate of rings.py (≤ 20 or 40 cards per profile) misses the
    Round 1 ring outright: D2c2006f554c8 spans 52 cards. What separates it from
    a browser default is not size but *shape* — every one of its 124 uses is
    behind an anonymous proxy, and its cards adopt it in bursts of dozens within
    days. A default browser is adopted steadily and almost never proxied.
    """
    ep = _episode(txns, as_of)
    best: TypologyHit | None = None
    for dk in {t.device_key for t in ep if t.device_key and t.online}:
        on_card = [t for t in ep if t.device_key == dk]
        first = min(t.ts for t in on_card)
        if any(t.device_key == dk for t in _before(txns, first)):
            continue  # not new to this account
        if not covered(src, first - timedelta(days=30)):
            continue  # an adoption "burst" at the start of the data is just the data starting
        uses = src.txns_by_device(dk, as_of - timedelta(days=180), as_of)
        first_use: dict[str, datetime] = {}
        for t in uses:
            first_use.setdefault(t.card_id, t.ts)
        joined = sorted(c for c, ts in first_use.items()
                        if c != card and abs(ts - first) <= timedelta(days=30))
        if len(joined) < 3:
            continue
        recent = [t for t in uses if abs(t.ts - first) <= timedelta(days=30)]
        proxy_share = sum(t.anon_proxy for t in recent) / len(recent)
        if proxy_share < 0.5:
            continue
        proxied = sum(t.anon_proxy for t in on_card)
        hit = TypologyHit(
            key="shared_device_ring",
            pattern="undocumented",
            description=(
                f"Device profile {dk}, never seen on this account before {first:%Y-%m-%d}, made "
                f"{len(on_card)} online purchase(s) here; {len(joined)} other cards began using the same profile "
                f"within 30 days, and {proxy_share:.0%} of its uses in that window were behind an anonymous proxy."
            ),
            txn_ids=_ids(on_card),
            connected_card_ids=tuple(joined),
            shared=dk,
            strength=0.95 if proxied else 0.75,
            evidence={"device_key": dk, "n_joined": len(joined), "proxy_share": round(proxy_share, 3)},
        )
        if best is None or len(hit.connected_card_ids) > len(best.connected_card_ids):
            best = hit
    return best


# ---------------------------------------------------------------------------
# 3. Dormant card waking
# ---------------------------------------------------------------------------


def dormant_wake(card: str, txns: Sequence[Txn], src: TxnSource, as_of: datetime) -> TypologyHit | None:
    ordered = sorted(txns, key=lambda t: t.ts)
    ep = _episode(ordered, as_of)
    if len(ep) < 2:
        return None
    start = ep[0]
    prior = _before(ordered, start.ts)
    if not prior or start.ts - prior[-1].ts < DORMANT_GAP:
        return None
    online = [t for t in ep if t.online]
    if len(online) < 2:
        return None
    base = _median([t.amt for t in prior])
    spend = sum(t.amt for t in online)
    if max(t.amt for t in online) < max(3 * base, 100.0):
        return None
    gap = (start.ts - prior[-1].ts).days
    return TypologyHit(
        key="dormant_reactivation",
        pattern="undocumented",
        description=(
            f"The card was silent for {gap} days (last use {prior[-1].ts:%Y-%m-%d}), then made "
            f"{len(online)} online purchases totalling ${spend:,.2f} against a prior median of ${base:,.2f}."
        ),
        txn_ids=_ids(online),
        shared="",
        strength=0.85,
        evidence={"gap_days": gap, "prior_median": base, "n": len(online)},
    )


# ---------------------------------------------------------------------------
# 4. Escalating charges
# ---------------------------------------------------------------------------


def escalation(card: str, txns: Sequence[Txn], src: TxnSource, as_of: datetime) -> TypologyHit | None:
    ep = sorted((t for t in _episode(txns, as_of) if t.online), key=lambda t: t.ts)
    prior = _before(txns, ep[0].ts) if ep else []
    base = _median([t.amt for t in prior]) or 50.0
    # Consecutive online charges only: a longest-increasing-subsequence finds a
    # "ladder" on any busy card, which is what the first backtest showed.
    best: list[Txn] = []
    run: list[Txn] = []
    for t in ep:
        if run and t.amt > run[-1].amt * 1.1 and t.ts - run[0].ts <= timedelta(days=12):
            run.append(t)
        else:
            run = [t]
        if len(run) > len(best):
            best = list(run)
    if len(best) < 4 or best[-1].amt < 4 * best[0].amt or best[-1].amt < 5 * base:
        return None
    return TypologyHit(
        key="escalating_charges",
        pattern="undocumented",
        description=(
            f"{len(best)} online charges in {(best[-1].ts - best[0].ts).days + 1} days, each larger than the "
            f"last, from ${best[0].amt:,.2f} to ${best[-1].amt:,.2f} against a usual ${base:,.2f} — "
            "probing how much the card will bear."
        ),
        txn_ids=_ids(best),
        strength=0.85,
        evidence={"first": best[0].amt, "last": best[-1].amt, "base": base},
    )


# ---------------------------------------------------------------------------
# 5. Sub-threshold micro charges
# ---------------------------------------------------------------------------


def _is_recurring(ts: Sequence[Txn]) -> bool:
    """Same price at a steady cadence is a subscription, not skimming."""
    if len(ts) < 3 or not _identical([t.amt for t in ts]):
        return False
    s = sorted(ts, key=lambda t: t.ts)
    gaps = [(b.ts - a.ts).total_seconds() / 86400 for a, b in zip(s, s[1:])]
    return max(gaps) - min(gaps) <= 1.5


def micro_charges(card: str, txns: Sequence[Txn], src: TxnSource, as_of: datetime) -> TypologyHit | None:
    ep = _episode(txns, as_of)
    micro = [t for t in ep if t.amt < MICRO_MAX]
    if len(micro) < 4 or _is_recurring(micro):
        return None
    span = max(t.ts for t in micro) - min(t.ts for t in micro)
    if span < timedelta(days=3):
        return None  # a tight cluster is card testing, handled below
    prior = _before(txns, min(t.ts for t in micro))
    if len([t for t in prior if t.amt < MICRO_MAX]) >= 3:
        return None  # this card has always made small purchases
    if prior and _median([t.amt for t in prior]) < MICRO_CARD_MIN_MEDIAN:
        return None
    by_card: dict[str, list[Txn]] = defaultdict(list)
    for t in src.txns_in_band(0.0, MICRO_MAX, as_of - EPISODE_WINDOW, as_of):
        if t.card_id != card:
            by_card[t.card_id].append(t)
    peers = sorted(
        c for c, ts in by_card.items()
        if len(ts) >= 4 and not _is_recurring(ts)
        and max(t.ts for t in ts) - min(t.ts for t in ts) >= timedelta(days=3)
    )
    total = round(sum(t.amt for t in micro), 2)
    return TypologyHit(
        key="micro_charges",
        pattern="undocumented",
        description=(
            f"{len(micro)} charges under ${MICRO_MAX:.0f} spread over {span.days} days, totalling ${total:,.2f}, "
            "on a card that does not normally make purchases that small — each too small to trip any review"
            + (f"; {len(peers)} other cards show the same drip in the same window" if peers else "") + "."
        ),
        txn_ids=_ids(micro),
        connected_card_ids=tuple(peers),
        shared=f"recurring sub-${MICRO_MAX:.0f} charges" if peers else "",
        strength=0.9 if len(peers) >= 2 else 0.6,
        evidence={"n": len(micro), "total": total, "n_peers": len(peers)},
    )


# ---------------------------------------------------------------------------
# 6. Many cards paying one recipient
# ---------------------------------------------------------------------------


def recipient_concentration(card: str, txns: Sequence[Txn], src: TxnSource, as_of: datetime) -> TypologyHit | None:
    ep = _episode(txns, as_of)
    best: TypologyHit | None = None
    for r in {t.r_email for t in ep if t.r_email}:
        mine = [t for t in ep if t.r_email == r]
        first = min(t.ts for t in mine)
        if any(t.r_email == r for t in _before(txns, first)):
            continue  # this card has paid them before
        if not covered(src, as_of - timedelta(days=181)):
            continue  # no six months of history to call a recipient "new" against
        recent = src.txns_by_recipient(r, as_of - timedelta(days=31), as_of)
        prior = src.txns_by_recipient(r, as_of - timedelta(days=181), as_of - timedelta(days=31))
        recent_cards = {t.card_id for t in recent} - {card}
        prior_cards = {t.card_id for t in prior}
        # Common in either window means a mail provider. The recent window matters
        # too: before ~January 2017 the prior window runs off the start of the
        # data, and gmail.com looked like a brand-new recipient on 2,640 cards.
        if len(prior_cards) > RECIPIENT_COMMON_CARDS or len(recent_cards) > RECIPIENT_COMMON_CARDS:
            continue
        new_cards = recent_cards - prior_cards
        monthly_prior = len(prior_cards) / 5
        if len(new_cards) < RECIPIENT_MIN_CARDS or len(new_cards) < 4 * max(monthly_prior, 1):
            continue
        hit = TypologyHit(
            key="recipient_concentration",
            pattern="undocumented",
            description=(
                f"{len(mine)} payment(s) to recipient {r}, which this card had never paid; {len(new_cards)} other "
                f"cards began paying the same recipient in the last month, against about "
                f"{monthly_prior:.1f} a month before."
            ),
            txn_ids=_ids(mine),
            connected_card_ids=tuple(sorted(new_cards)),
            shared=r,
            strength=0.9,
            evidence={"recipient": r, "new_cards": len(new_cards), "monthly_prior": monthly_prior},
        )
        if best is None or len(hit.connected_card_ids) > len(best.connected_card_ids):
            best = hit
    return best


# ---------------------------------------------------------------------------
# 7. Card testing (documented)
# ---------------------------------------------------------------------------


def card_testing(card: str, txns: Sequence[Txn], src: TxnSource, as_of: datetime) -> TypologyHit | None:
    """R5 as written: three or more small online authorisations within an hour,
    followed by a larger purchase. (Two probes in three hours fired on organic
    cards in the backtest.)"""
    ep = sorted((t for t in _episode(txns, as_of) if t.online), key=lambda t: t.ts)
    for i, t in enumerate(ep):
        if t.amt >= 10:
            continue
        hour = [u for u in ep[i:] if u.ts - t.ts <= timedelta(hours=1)]
        probes = [u for u in hour if u.amt < 10]
        if len(probes) < 3 or _is_recurring(probes):
            continue
        big = [u for u in ep[i:] if u.amt >= 50 and probes[-1].ts <= u.ts <= probes[-1].ts + timedelta(hours=3)]
        if big:
            return TypologyHit(
                key="card_testing",
                pattern="card_testing",
                description=(
                    f"{len(probes)} online authorisations under $10 within an hour, followed "
                    f"{int((big[0].ts - probes[-1].ts).total_seconds() // 60)} minutes later by ${big[0].amt:,.2f} (R5)."
                ),
                txn_ids=_ids(probes + big[:1]),
                strength=0.9,
            )
    return None


Detector = Callable[[str, Sequence[Txn], TxnSource, datetime], TypologyHit | None]

DETECTORS: dict[str, Detector] = {
    "structuring_under_threshold": structuring,
    "shared_device_ring": device_ring,
    "dormant_reactivation": dormant_wake,
    "escalating_charges": escalation,
    "micro_charges": micro_charges,
    "recipient_concentration": recipient_concentration,
    "card_testing": card_testing,
}


def run_detectors(card: str, src: TxnSource, as_of: datetime) -> list[TypologyHit]:
    txns = src.card_txns(card, as_of)
    hits = [h for d in DETECTORS.values() if (h := d(card, txns, src, as_of))]
    return sorted(hits, key=lambda h: (-h.strength, -len(h.connected_card_ids)))
