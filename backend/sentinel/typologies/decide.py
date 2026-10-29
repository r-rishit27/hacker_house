"""The decision layer: typologies, look-alikes, the trigger rules, and — only for
what is left — the reasoning model.

Order matters, and each step can only make the answer *more* cautious about
harming a customer:

1. A typology detector anchored on this case fires decisively  → ``fraud``.
2. A customer denied the charge (R2)                             → ``fraud``.
   The model's probability is recorded but cannot overrule R2: on Round 1 it
   was anti-informative on customer reports.
3. Score or analyst trigger, every anomaly on the flagged transaction
   explained by the card's own history, nothing fired             → ``legitimate``.
4. Anything else is tricky                                        → the model.
   Its ``fraud`` stands only at p ≥ 0.80 *and* when the deterministic layer
   corroborates (two or more unexplained anomalies over a multi-charge
   episode); otherwise ``uncertain`` with R1 verification, which costs nothing
   on a legitimate customer. Uncertain probabilities are capped at 0.69 so
   SarPolicy never reads them as "strongly suspected".
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sentinel.typologies.adjudicator import Adjudication, adjudicate_any, scan_injection
from sentinel.typologies.anomalies import anomalies
from sentinel.typologies.episode import scope as scope_episode
from sentinel.typologies.detectors import SPECIFIC_DEVICE_MAX_CARDS, run_detectors
from sentinel.typologies.explain import explain
from sentinel.typologies.model import Txn, TxnSource, TypologyHit

#: A hit is about *this* case if it touches the flagged transaction or lies within this of it.
ANCHOR = timedelta(days=7)
DECISIVE = 0.8
MODEL_FRAUD_FLOOR = 0.80
VERY_LOW = 0.10

#: Every rule added for Round 2 can be switched off, so its contribution can be
#: measured by ablation (eval/overfit_check.py). All are on in production.
RULES = {"R1_weak_single": True, "R7": True, "new_to_bank": True, "very_low": True}


@dataclass
class Decision:
    verdict: str
    fraud_probability: float
    pattern: str
    pattern_description: str
    affected_txn_ids: list[int]
    connected_card_ids: list[str]
    shared: str
    reasons: list[str]
    unexplained: list[str]
    explanations: list[str]
    hits: list[TypologyHit]
    decided_by: str  # "typology" | "rule_R2" | "explained" | "model" | "model_failed_closed"
    injection_spans: list[str] = field(default_factory=list)
    precedent: list[dict] = field(default_factory=list)
    #: Tokens of the GraphRAG query embedding, and why vector precedent did not run if it did not.
    embed_tokens: int = 0
    vector_status: str = ""
    adjudication: Adjudication | None = None


def _anchored(h: TypologyHit, f: Txn, by_id: dict[int, Txn]) -> bool:
    if f.txn_id in h.txn_ids:
        return True
    return any(abs(by_id[i].ts - f.ts) <= ANCHOR for i in h.txn_ids if i in by_id)


def baseline(hist: list[Txn]) -> dict:
    if not hist:
        return {"n_txns": 0}
    amts = [t.amt for t in hist]
    return {
        "n_txns": len(hist),
        "first_seen": hist[0].ts, "last_seen": hist[-1].ts,
        "median_amt": round(statistics.median(amts), 2),
        "p95_amt": round(sorted(amts)[int(0.95 * (len(amts) - 1))], 2),
        "channels": dict(Counter(t.channel for t in hist)),
        "top_products": dict(Counter(t.product for t in hist).most_common(4)),
        "top_regions": dict(Counter(t.addr1 for t in hist if t.addr1).most_common(5)),
        "emails": dict(Counter(t.p_email for t in hist if t.p_email).most_common(4)),
        "n_devices": len({t.device_key for t in hist if t.device_key}),
        "n_proxy_txns": sum(bool(t.proxy) for t in hist),
        "n_night_txns": sum(0 <= t.ts.hour < 6 for t in hist),
    }


def _row(t: Txn) -> dict:
    return {"txn_id": t.txn_id, "ts": t.ts, "amt": t.amt, "product": t.product, "channel": t.channel,
            "region": t.addr1, "p_email": t.p_email, "r_email": t.r_email, "device": t.device_key,
            "device_new_flag": t.device_new, "proxy": t.proxy, "risk_score": t.risk_score}


def _episode(f: Txn, hist: list[Txn], after: list[Txn]) -> list[int]:
    """The flagged charge plus the charges within three days either side of it, on
    the same channel, from a device new to the card or (card-present) in the same
    region. An alert can land on any charge of a burst — the first or the last —
    so the episode looks both ways. January's new-device fraud used three
    different new devices in one day, so "the same device" was too narrow."""
    lo = f.ts - timedelta(days=3)
    base = [t for t in hist if t.ts < lo]
    known_dev = {t.device_key for t in base if t.device_key}
    ids = [f.txn_id]
    for t in [*(t for t in hist if t.ts >= lo), *after]:
        if abs(t.ts - f.ts) > timedelta(days=3) or t.channel != f.channel:
            continue
        if (t.device_key and t.device_key not in known_dev) or (not f.online and t.addr1 == f.addr1):
            ids.append(t.txn_id)
    return ids


#: Signals that, alone on one charge, R1 calls weak: verify, do not block.
WEAK_SIGNALS = frozenset({"device_new", "product_new", "email_new", "amount_high", "late_night"})
#: Days and cards under which a device profile counts as new to the bank.
NEW_TO_BANK_DAYS, NEW_TO_BANK_CARDS = 14, 5


def _new_to_bank(f: Txn, src: TxnSource) -> bool:
    if not f.device_key:
        return False
    start = getattr(src, "data_start", None)
    if start is not None and f.ts - start < timedelta(days=60):
        return False  # every device is "new to the bank" in the first weeks of data
    uses = src.txns_by_device(f.device_key, f.ts - timedelta(days=180), f.ts + timedelta(seconds=1))
    if not uses:
        return False
    return f.ts - uses[0].ts <= timedelta(days=NEW_TO_BANK_DAYS) and len({t.card_id for t in uses}) <= NEW_TO_BANK_CARDS


def _pattern_for(f: Txn, unexplained) -> str:
    keys = {a.key for a in unexplained}
    if not f.online:
        return "out_of_region_use"  # card-present fraud: the card was used where the cardholder was not
    if "email_new" in keys and "device_new" in keys:
        return "account_takeover"
    if "device_new" in keys or "new_device_behind_proxy" in keys:
        return "card_not_present_new_device"
    return "card_not_present_fraud"


def decide(
    *, case_id: str, trigger_type: str, trigger_text: str, card_id: str, flagged_txn_id: int,
    as_of: datetime, src: TxnSource, prior_cases: list[dict] | None = None, use_model: bool = True,
    model_client=None,
) -> Decision:
    all_txns = src.card_txns(card_id, as_of)
    by_id = {t.txn_id: t for t in all_txns}
    f = by_id.get(flagged_txn_id) or next((t for t in src.card_txns(card_id, as_of + timedelta(days=1))
                                            if t.txn_id == flagged_txn_id), None)
    if f is None:
        raise LookupError(f"flagged transaction {flagged_txn_id} not on card {card_id}")
    hist = [t for t in all_txns if t.ts < f.ts]
    after = [t for t in all_txns if t.ts > f.ts]
    hits = [h for h in run_detectors(card_id, src, as_of) if _anchored(h, f, by_id)]
    exps = explain(f, hist, after)
    ex_keys = {e.key for e in exps}
    anoms = anomalies(f, hist)
    unexplained = [a for a in anoms if not set(a.explained_by) & ex_keys]
    spans = scan_injection(trigger_text)

    found: dict = {"precedent": [], "embed_tokens": 0, "vector_status": ""}  # filled once retrieval has run

    def out(verdict, p, pattern, desc, affected, decided_by, reasons, hit=None, adj=None) -> Decision:
        return Decision(
            verdict, round(p, 4), pattern, desc, sorted(set(affected), key=lambda i: by_id[i].ts if i in by_id else f.ts),
            list(hit.connected_card_ids) if hit else [], hit.shared if hit else "", reasons,
            [f"{a.key}: {a.detail}" for a in unexplained], [e.reason for e in exps], hits, decided_by,
            injection_spans=spans, adjudication=adj, precedent=found["precedent"],
            embed_tokens=found["embed_tokens"], vector_status=found["vector_status"],
        )

    # 1. A decisive typology anchored on this case.
    strong = [h for h in hits if h.strength >= DECISIVE]
    if strong:
        h = strong[0]
        affected = list(h.txn_ids)
        if f.txn_id not in affected and any(abs(by_id[i].ts - f.ts) <= timedelta(days=1) for i in affected if i in by_id):
            affected.append(f.txn_id)  # the flagged charge sits inside the episode the detector found
        p = 0.93 + (0.04 if h.coordinated else 0.0)
        return out("fraud", p, h.pattern, h.description, affected, "typology",
                   [f"typology {h.key} fired (strength {h.strength})"], hit=h)

    # Precedent the decision model reads: closed cases sharing this card, a device
    # profile it used, or a recipient it paid (retrieval by shared entity).
    window = [t for t in all_txns if t.ts >= f.ts - ANCHOR]
    precedent = []
    if hasattr(src, "linked_closed_cases"):
        # Only device profiles specific enough to mean something: linking through a
        # browser default pulled unrelated fraud cases in, and the model read them
        # as coordination (P(coordinated) 0.96 on a cleared case).
        devices = sorted({t.device_key for t in window if t.device_key
                          and src.device_card_count(t.device_key, as_of) <= SPECIFIC_DEVICE_MAX_CARDS})
        recipients = sorted({t.r_email for t in window if t.r_email}
                            - {"gmail.com", "yahoo.com", "hotmail.com", "anonymous.com", "outlook.com", "aol.com"})
        precedent = src.linked_closed_cases(card_id, devices, recipients, as_of)
    # GraphRAG: closed cases whose notes read like this one (vector search over
    # ClosedCase.emb on the graph), fused with the graph-linked ones by rank.
    vector_hits, vector_error = [], ""
    if getattr(src, "vector", None) is not None:
        from sentinel.typologies.vector import describe, fuse

        try:
            vector_hits = src.vector.search(describe(f, [f"{a.key}: {a.detail}" for a in unexplained],
                                                     [e.reason for e in exps], hits), as_of)
        except Exception as exc:  # noqa: BLE001 — supporting evidence; recorded, never pretended
            vector_error = f"vector precedent unavailable: {type(exc).__name__}: {str(exc)[:160]}"
        precedent = fuse(precedent, vector_hits)
    found["precedent"] = precedent
    if getattr(src, "vector", None) is not None:
        found["embed_tokens"] = getattr(src.vector, "last_tokens", 0)
        found["vector_status"] = vector_error or "ok"
    lookalikes = src.cleared_lookalikes([a.key for a in anoms], as_of) if hasattr(src, "cleared_lookalikes") else []
    context = src.history_context() if hasattr(src, "history_context") else {}
    episode_ids = _episode(f, hist, after)

    bundle = {
        "case_id": case_id, "trigger_type": trigger_type, "trigger_text": trigger_text,
        "flagged_txn": _row(f), "card_baseline": baseline(hist),
        "recent_history": [_row(t) for t in hist[-15:]],
        "episode_after_flagged": [_row(t) for t in after[:20]],
        "unexplained_anomalies": [f"{a.key}: {a.detail}" for a in unexplained],
        "history_explains": [e.reason for e in exps],
        "typology_hits": [{"key": h.key, "description": h.description, "strength": h.strength} for h in hits],
        "prior_closed_cases_on_card": prior_cases or [],
        "precedent_cases": precedent,
        "vector_precedent_status": vector_error or ("ok" if vector_hits else "not available on this source"),
        "cleared_lookalike_precedents": lookalikes,
        "bank_history_context": context,
        "_citable_txn_ids": [f.txn_id, *(t.txn_id for t in hist[-15:]), *(t.txn_id for t in after[:20])],
        "_episode_txn_ids": episode_ids,
    }

    # 3. Nothing fired and the history explains everything unusual.
    if trigger_type != "customer_report" and not unexplained and not hits:
        return out("legitimate", 0.08, "none", "", [], "explained",
                   ["no typology fired; every anomaly on the flagged transaction is explained by the card's history"])

    # 3b. R1: one charge resting on weak signals only. Every one of the bank's 900
    # score-raised alerts was cleared, and a blocked innocent costs more than a
    # missed fraud, so this is legitimate with monitoring — not a block, not a
    # coin-flip "uncertain". A device new to the bank, a proxy or a typology
    # takes the case out of this branch.
    unexplained_keys = {a.key for a in unexplained}
    if (RULES["R1_weak_single"] and trigger_type == "risk_score" and not hits and len(episode_ids) == 1
            and unexplained_keys <= WEAK_SIGNALS and not f.proxy and not _new_to_bank(f, src)):
        return out("legitimate", 0.15, "none", "", [], "rule_R1_weak_single",
                   ["R1: a single score-raised charge resting only on weak signals ("
                    + ", ".join(sorted(unexplained_keys)) + "); no typology, no proxy, no device new to the bank, "
                    "no further charges — the bank cleared every such alert in its history"])

    adj = adjudicate_any(bundle, post=model_client) if use_model else None
    model_ok = adj is not None and adj.accepted

    # 2. R2: the customer has denied the charge.
    if trigger_type == "customer_report":
        # R7: a dispute on a card with an earlier confirmed fraud, where the charge
        # fits the card's usual product and only the device is odd, is verified,
        # not blocked (HHG-004: "had a closed fraud case earlier; this fits").
        prior_fraud_same_card = any(p.get("outcome") == "confirmed_fraud" and p.get("card_id", card_id) == card_id
                                    for p in (prior_cases or []))
        share = sum(t.product == f.product for t in hist) / len(hist) if hist else 0.0
        if (RULES["R7"] and prior_fraud_same_card and not hits and unexplained_keys == {"device_new"} and share >= 0.5
                and len(episode_ids) == 1 and not f.proxy and not _new_to_bank(f, src)):
            # R7 is the policy's "disputed but legitimate": verify and inform, no block.
            return out("legitimate", 0.25, "none", "", [], "rule_R7",
                       ["R7: the cardholder disputes a charge that fits their own pattern (product "
                        f"{f.product} is {share:.0%} of their history) on a card with an earlier confirmed fraud — "
                        "verified and informed, not blocked"], adj=adj)
        # R2 governs a denial: block, no verification step. The model may not
        # overrule it — on Round 1 it scored a real fraud 0.38 and a cleared R7
        # dispute 0.98, i.e. it was anti-informative on customer reports. Its
        # probability is still recorded as evidence; it does not set the verdict.
        affected = scope_episode(f, all_txns)
        pattern = adj.pattern if model_ok and adj.verdict == "fraud" and adj.pattern != "none" else _pattern_for(f, unexplained)
        p = max(adj.fraud_probability, 0.85) if model_ok and adj.verdict == "fraud" else 0.85
        return out("fraud", p, pattern, "", affected, "rule_R2",
                   ["customer denied the charge (R2)"], adj=adj)

    # 4. Tricky: the model's call, with a floor before it may harm anyone.
    if not model_ok:
        return out("uncertain", 0.5, "none", "", [f.txn_id], "model_failed_closed",
                   ["unexplained anomalies and no decisive evidence; decision model "
                    + ("not used" if adj is None else f"rejected/failed: {adj.rejected}") + " — verify (R1)"], adj=adj)
    # The model alone may not move a score-only alert to a block. It needs the
    # deterministic layer to agree that more than one thing is unexplained, and
    # an episode rather than a single charge. Round 1: the model at 0.88-0.98 on
    # three new-device alerts the bank had cleared.
    # A device profile new to the bank is the one thing that can stand in for a
    # multi-charge episode: a cardholder's new phone is usually a common model the
    # bank has seen on many cards; a fraudster's set-up often is not (HHG-019).
    new_to_bank = RULES["new_to_bank"] and _new_to_bank(f, src)
    # Two weak anomalies over a few charges was not enough: on held-out cleared
    # alerts the model then called 10% of innocent customers fraud. A model fraud
    # call now needs a *strong* corroborator the rules can see for themselves.
    # And it needs more than one charge. Held out, the bank cleared single-charge
    # alerts with a new device behind a proxy (CC-1024, CC-1165) and with a device
    # new to the bank (CC-3385) — the same shape as HHG-019, which was fraud. One
    # charge cannot tell them apart, so one charge is verified, not blocked.
    strong = new_to_bank or "new_device_behind_proxy" in unexplained_keys
    corroborated = (strong and len(bundle["_episode_txn_ids"]) >= 2
                    and len(unexplained_keys | ({"device_new_to_bank"} if new_to_bank else set())) >= 2)
    if adj.verdict == "fraud" and adj.fraud_probability >= MODEL_FRAUD_FLOOR and corroborated:
        pattern = adj.pattern if adj.pattern != "none" else _pattern_for(f, unexplained)
        return out("fraud", adj.fraud_probability, pattern, adj.rationale if pattern == "undocumented" else "",
                   scope_episode(f, all_txns, extra=adj.cited_txn_ids),
                   "model", ["decision model: fraud", adj.rationale], adj=adj)
    # A very low P(fraud) clears on its own; just under the line it needs the model
    # to also say the history explains the anomaly (adjudicator.verdict_from).
    # (The raw score, not adj.fraud_probability, which verdict_from clamps to 0.30-0.69.)
    raw_p = (adj.raw.get("probabilities") or {}).get("is_fraud", adj.fraud_probability)
    if not hits and (adj.verdict == "legitimate" or (RULES["very_low"] and raw_p <= VERY_LOW)):
        return out("legitimate", round(min(raw_p, adj.fraud_probability), 4), "none", "", [], "model",
                   ["decision model: legitimate", adj.rationale], adj=adj)
    # 0.69, not 0.70: SarPolicy reads >= 0.70 as "strongly suspected" and would file.
    return out("uncertain", min(max(adj.fraud_probability, 0.3), 0.69), "none", "", [f.txn_id], "model",
               ["decision model below the fraud floor; verify before acting (R1)", adj.rationale], adj=adj)
