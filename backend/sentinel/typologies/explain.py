"""Does the card's own history explain what looks wrong with this transaction?

Each explainer is one sentence from the bank's cleared-case notes turned into a
test. All ten of Round 1's cleared cases are covered by one of these, and seven
of them we called fraud or leaned that way:

* "Device marked New, but amount and product fit the cardholder"   (HHG-005, 020)
* "Amount far above average, but product and email match history"  (HHG-002, 010, 015)
* "Closed fraud case earlier this year; this purchase fits"        (HHG-004)
* "Late-night purchase; the cardholder has prior late-night activity" (HHG-013)
* "Anonymous proxy; the cardholder's other purchases show the same proxy" (HHG-017)
* "Card-present purchases in one region over consecutive days: travel" (HHG-001, 012)

An explanation does not clear a case on its own. It removes one anomaly from
the case; the decision layer clears only when every anomaly is explained and no
typology detector fired.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import timedelta

from sentinel.typologies.model import Explanation, Txn

#: Below this many prior transactions the card has no baseline to explain anything with.
MIN_HISTORY = 5


def _share(values: Sequence[object], v: object) -> float:
    return Counter(values)[v] / len(values) if values else 0.0


def _p95(xs: Sequence[float]) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(0.95 * len(s)))] if s else 0.0


def new_device_fits(f: Txn, hist: Sequence[Txn]) -> Explanation | None:
    if f.device_new != "New" and not (f.device_key and all(t.device_key != f.device_key for t in hist)):
        return None
    amts = [t.amt for t in hist]
    if f.amt <= max(_p95(amts), 1.5 * statistics.median(amts)) and _share([t.product for t in hist], f.product) >= 0.1:
        return Explanation(
            "new_device_fits", "device new to the account",
            f"device is new, but ${f.amt:,.2f} is within the card's usual range "
            f"(95th percentile ${_p95(amts):,.2f}) and product {f.product} is one it buys regularly",
        )
    return None


def high_amount_matches(f: Txn, hist: Sequence[Txn]) -> Explanation | None:
    amts = [t.amt for t in hist]
    med = statistics.median(amts)
    if f.amt <= 3 * med:
        return None
    same_product = _share([t.product for t in hist], f.product) >= 0.2
    emails = {t.p_email for t in hist if t.p_email}
    same_email = f.p_email is None or f.p_email in emails
    if same_product and same_email and not f.anon_proxy:
        return Explanation(
            "high_amount_matches", "amount far above the card's average",
            f"${f.amt:,.2f} is {f.amt / med:.1f}x the card's median ${med:,.2f}, but the product "
            f"({f.product}) and e-mail ({f.p_email or 'none'}) match the cardholder's history",
        )
    return None


def late_night_habit(f: Txn, hist: Sequence[Txn]) -> Explanation | None:
    if not 0 <= f.ts.hour < 6:
        return None
    night = [t for t in hist if 0 <= t.ts.hour < 6]
    if len(night) >= 3:
        return Explanation(
            "late_night_habit", "late-night purchase",
            f"{len(night)} earlier purchases on this card were also made between midnight and 6am",
            tuple(t.txn_id for t in night[-5:]),
        )
    return None


def proxy_habit(f: Txn, hist: Sequence[Txn]) -> Explanation | None:
    if not f.proxy:
        return None
    # Same proxy kind *from a device the card already uses*. A new device behind
    # an anonymous proxy is not a habit, whatever the card did before.
    known_devices = {t.device_key for t in hist if t.device_key}
    if f.device_key and f.device_key not in known_devices:
        return None
    same = [t for t in hist if t.proxy == f.proxy]
    if len(same) >= 2:
        return Explanation(
            "proxy_habit", "proxy on the identity record",
            f"{len(same)} earlier purchases on this card used a proxy too",
            tuple(t.txn_id for t in same[-5:]),
        )
    return None


def travel_cluster(f: Txn, hist: Sequence[Txn], after: Sequence[Txn] = ()) -> Explanation | None:
    if f.online or not f.addr1:
        return None
    if f.addr1 in {t.addr1 for t in hist}:
        return None
    around = [t for t in (*hist, *after) if t.addr1 == f.addr1 and not t.online
              and abs(t.ts - f.ts) <= timedelta(days=4) and t.txn_id != f.txn_id]
    days = {t.ts.date() for t in around} | {f.ts.date()}
    if len(around) >= 2 and len(days) >= 2:
        return Explanation(
            "travel_cluster", "card-present use in a new region",
            f"{len(around) + 1} card-present purchases in region {f.addr1} over {len(days)} consecutive days — "
            "the shape of travel, not of a cloned card",
            tuple(t.txn_id for t in around),
        )
    return None


def recurring_charge(f: Txn, hist: Sequence[Txn]) -> Explanation | None:
    same = sorted((t for t in hist if abs(t.amt - f.amt) <= 0.01 * max(f.amt, 1)), key=lambda t: t.ts)
    if len(same) < 2:
        return None
    seq = [*same, f]
    gaps = [(b.ts - a.ts).total_seconds() / 86400 for a, b in zip(seq, seq[1:])]
    if max(gaps) - min(gaps) <= 1.5 and min(gaps) >= 6:
        return Explanation(
            "recurring_charge", "repeated small charge",
            f"${f.amt:,.2f} charged {len(seq)} times every ~{statistics.mean(gaps):.0f} days — a subscription",
            tuple(t.txn_id for t in same),
        )
    return None


Explainer = Callable[[Txn, Sequence[Txn]], Explanation | None]

EXPLAINERS: dict[str, Explainer] = {
    "new_device_fits": new_device_fits,
    "high_amount_matches": high_amount_matches,
    "late_night_habit": late_night_habit,
    "proxy_habit": proxy_habit,
    "recurring_charge": recurring_charge,
}


def explain(f: Txn, hist: Sequence[Txn], after: Sequence[Txn] = ()) -> list[Explanation]:
    """Every explanation the history supports for the flagged transaction ``f``.

    ``hist`` is the card's history strictly before ``f``; ``after`` is what the
    card did between ``f`` and the as-of time (travel is judged on both sides).
    """
    if len(hist) < MIN_HISTORY:
        return []
    out = [e for fn in EXPLAINERS.values() if (e := fn(f, hist))]
    if (e := travel_cluster(f, hist, after)):
        out.append(e)
    return out
