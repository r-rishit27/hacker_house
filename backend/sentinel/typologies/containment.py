"""Containment planner (challenge 7): the smallest set of blocks that stops a ring.

Knowing which cards a ring touched does not stop the loss, and blocking every
one of them harms people who did nothing wrong — a card that met a ring device
once is still the cardholder's card. For a ring found by a detector, this plans:

* per linked card: the ring's exposure on it (the transactions tied to the
  shared thing — the device, the recipient, the burst band, the micro-charges),
  and the card's *own* ordinary spend over the same weeks, which a block would
  also stop;
* ``BLOCK_CARD`` where the block stops more ring exposure than the stated cost of
  disrupting a cardholder (``DISRUPTION_COST``), or where the ring is most of what
  the card does; ``MONITOR_CARD`` everywhere else — maximising exposure cut minus
  disruption, card by card;
* every customer an action touches, named, with what the block would interrupt.

The plan is a recommendation: each BLOCK_CARD still needs its L1/L2 approval.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sentinel.typologies.detectors import MICRO_MAX, STRUCTURING_BAND
from sentinel.typologies.model import Txn, TxnSource, TypologyHit

#: The stated price of disrupting one cardholder who also uses the card normally.
#: A block is worth it when it stops more ring exposure than this, or when the ring
#: is most of what the card does (nobody legitimate to disrupt).
DISRUPTION_COST = float(__import__("os").getenv("CONTAINMENT_DISRUPTION_COST", "100"))
RING_DOMINANT = 0.5
WINDOW = timedelta(days=45)


def _ring_txns(hit: TypologyHit, txns: list[Txn]) -> list[Txn]:
    if hit.key == "shared_device_ring":
        return [t for t in txns if t.device_key == hit.shared]
    if hit.key == "recipient_concentration":
        return [t for t in txns if t.r_email == hit.shared]
    if hit.key == "structuring_under_threshold":
        th = hit.evidence.get("threshold", 500.0)
        return [t for t in txns if t.online and th * STRUCTURING_BAND <= t.amt < th]
    if hit.key == "micro_charges":
        return [t for t in txns if t.amt < MICRO_MAX]
    return []


def plan(hit: TypologyHit, card_id: str, src: TxnSource, as_of: datetime) -> dict:
    cards = [card_id, *hit.connected_card_ids]
    rows = []
    for c in cards:
        txns = [t for t in src.card_txns(c, as_of) if t.ts >= as_of - WINDOW]
        ring = _ring_txns(hit, txns) if c != card_id else [t for t in txns if t.txn_id in hit.txn_ids] or _ring_txns(hit, txns)
        ring_ids = {t.txn_id for t in ring}
        own = [t for t in txns if t.txn_id not in ring_ids]
        exp, own_spend = round(sum(t.amt for t in ring), 2), round(sum(t.amt for t in own), 2)
        share = exp / (exp + own_spend) if exp + own_spend else 0.0
        rows.append({"card_id": c, "customer_id": c.split("-K")[0], "ring_txn_ids": sorted(ring_ids),
                     "ring_exposure": exp, "own_spend_45d": own_spend, "own_txns_45d": len(own),
                     "ring_share": round(share, 3)})
    total = sum(r["ring_exposure"] for r in rows)
    covered = 0.0
    for r in sorted(rows, key=lambda r: -r["ring_exposure"]):
        disrupts = r["own_txns_45d"] > 0
        worth = r["ring_exposure"] > (DISRUPTION_COST if disrupts else 0) or r["ring_share"] >= RING_DOMINANT
        if r["ring_exposure"] > 0 and worth:
            r["action"], covered = "BLOCK_CARD", covered + r["ring_exposure"]
            r["reason"] = (f"stops ${r['ring_exposure']:,.2f} of ring activity ({r['ring_share']:.0%} of the card's "
                           f"spend), more than the ${DISRUPTION_COST:,.0f} cost of disrupting the cardholder")
        else:
            r["action"] = "MONITOR_CARD"
            r["reason"] = (f"ring exposure ${r['ring_exposure']:,.2f} does not justify disrupting a card used "
                           f"{r['own_txns_45d']} times normally; watch for further ring activity")
        r["touches_customer"] = (f"{r['customer_id']}: block also stops {r['own_txns_45d']} ordinary purchase(s) "
                                 f"worth ${r['own_spend_45d']:,.2f} in 45 days; reissue the card"
                                 if r["action"] == "BLOCK_CARD" else f"{r['customer_id']}: monitored, no disruption")
    blocked = [r for r in rows if r["action"] == "BLOCK_CARD"]
    return {
        "ring": hit.key, "shared": hit.shared, "as_of": as_of.isoformat(sep=" "), "cards": rows,
        "total_ring_exposure": round(total, 2), "exposure_cut": round(covered, 2),
        "coverage": round(covered / total, 3) if total else 0.0,
        "n_blocked": len(blocked), "n_monitored": len(rows) - len(blocked),
        "net_value": round(covered - DISRUPTION_COST * sum(1 for r in blocked if r["own_txns_45d"]), 2),
        "customers_disrupted": [r["customer_id"] for r in blocked if r["own_txns_45d"]],
        "naive_block_all": {"n_blocked": len(rows), "exposure_cut": round(total, 2),
                            "customers_disrupted": sum(1 for r in rows if r["own_txns_45d"]),
                            "net_value": round(total - DISRUPTION_COST * sum(1 for r in rows if r["own_txns_45d"]), 2)},
    }
