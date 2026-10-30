"""What is unusual about the flagged transaction, for this card.

Each anomaly is a fact about ``f`` against the card's own history, and each one
names the explainer in :mod:`sentinel.typologies.explain` that can account for
it. The decision layer clears a case only when every anomaly found here has an
explanation and no typology fired.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from sentinel.typologies.model import Txn


@dataclass(frozen=True, slots=True)
class Anomaly:
    key: str
    detail: str
    explained_by: tuple[str, ...]


def anomalies(f: Txn, hist: Sequence[Txn]) -> list[Anomaly]:
    out: list[Anomaly] = []
    if not hist:
        return [Anomaly("no_history", "the card has no earlier transactions", ())]
    amts = [t.amt for t in hist]
    med = statistics.median(amts)
    p95 = sorted(amts)[min(len(amts) - 1, int(0.95 * len(amts)))]
    devices = {t.device_key for t in hist if t.device_key}
    if f.device_key and (f.device_key not in devices or f.device_new == "New"):
        out.append(Anomaly("device_new", f"device {f.device_key} not seen on this card before", ("new_device_fits",)))
    if f.amt > max(p95, 3 * med):
        out.append(Anomaly("amount_high", f"${f.amt:,.2f} vs median ${med:,.2f}, p95 ${p95:,.2f}",
                           ("high_amount_matches", "recurring_charge")))
    regions = {t.addr1 for t in hist if t.addr1}
    if f.addr1 and regions and f.addr1 not in regions:
        out.append(Anomaly("region_new", f"billing region {f.addr1} new to the card", ("travel_cluster",)))
    if 0 <= f.ts.hour < 6:
        out.append(Anomaly("late_night", f"purchase at {f.ts:%H:%M}", ("late_night_habit",)))
    if f.proxy:
        out.append(Anomaly("proxy", f"identity record shows {f.proxy}", ("proxy_habit",)))
    if f.online and f.anon_proxy and f.device_key and f.device_key not in devices:
        out.append(Anomaly("new_device_behind_proxy", "a new device behind an anonymous proxy", ()))
    share = Counter(t.product for t in hist)[f.product] / len(hist)
    if share < 0.05:
        out.append(Anomaly("product_new", f"product {f.product} is {share:.0%} of the card's history", ()))
    emails = {t.p_email for t in hist if t.p_email}
    if f.p_email and emails and f.p_email not in emails:
        out.append(Anomaly("email_new", f"purchaser e-mail {f.p_email} not used on this card before", ()))
    return out
