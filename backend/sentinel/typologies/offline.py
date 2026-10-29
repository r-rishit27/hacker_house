"""Turn a :class:`Decision` into an answer file, with the published policy engine.

The decision layer says *what happened*; ``PolicyEngine`` — unchanged from
Round 1 — says *what to do about it*, twice: once before the evidence request
and once after the assumed reply. Nothing here picks an action.

Every evidence claim cites the detector or query that produced it, with its
parameters and the dataset it ran against, so a judge can re-run it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from sentinel.domain.answer import AnswerFile
from sentinel.domain.enums import Action, CustomerResponse
from sentinel.policy.engine import build_policy_engine
from sentinel.policy.state import CaseState
from sentinel.typologies.decide import Decision
from sentinel.typologies.model import TxnSource

DATASET = "lab.duckdb: transactions Jul 2016 - Jan 2017 + closed_cases_history (5,585)"
ENGINE = build_policy_engine()

SHARED_KIND = {
    "shared_device_ring": "a shared device profile",
    "recipient_concentration": "a recipient many cards began paying at once",
    "structuring_under_threshold": "the same structured burst on other cards",
    "micro_charges": "the same sub-threshold charges on other cards",
}


def _recs(state: CaseState) -> tuple[list[dict], bool, str]:
    out = ENGINE.evaluate(state)
    return [r.as_dict() for r in out.recommendations], out.sar.file, out.sar.reason


def _state(d: Decision, trigger: str, exposure: float, other_fraud: bool) -> CaseState:
    hit = next((h for h in d.hits if h.strength >= 0.8), None)
    coordinated = bool(hit and hit.coordinated)
    return CaseState(
        fraud_probability=d.fraud_probability,
        exposure_usd=round(exposure, 2),
        verdict=d.verdict,
        trigger_type=trigger,
        customer_response=CustomerResponse.DENIED if trigger == "customer_report" and d.verdict == "fraud" else None,
        independent_signals=max(1, len(d.hits) + len(d.unexplained)),
        pattern=d.pattern if d.verdict == "fraud" else "none",
        card_testing_sequence=any(h.key == "card_testing" for h in d.hits) and d.verdict == "fraud",
        shared_origin=coordinated,
        shared_origin_kind=SHARED_KIND.get(hit.key, "") if coordinated else "",
        connected_card_ids=list(d.connected_card_ids),
        other_customer_fraud=other_fraud,
        cards_with_confirmed_fraud=1 if d.verdict == "fraud" else 0,
        evidence_conflicts=d.verdict == "uncertain",
        undocumented_pattern=d.verdict == "fraud" and d.pattern == "undocumented",
    )


def _ev(claim: str, ref: str, ids: list, source: str = "graph") -> dict:
    return {"claim": claim, "source": source, "ref": ref, "entity_ids": [str(i) for i in ids]}


def build_answer(
    d: Decision, *, case_id: str, trigger: str, card_id: str, customer_id: str, flagged_txn_id: int,
    as_of: datetime, src: TxnSource, prior_cases: list[dict], tool_calls: int, latency_s: float,
) -> dict:
    txns = {t.txn_id: t for t in src.card_txns(card_id, as_of)}
    affected = [i for i in d.affected_txn_ids if i in txns] if d.verdict != "legitimate" else []
    affected.sort(key=lambda i: (txns[i].ts, i))
    exposure = round(sum(txns[i].amt for i in affected), 2)
    connected_fraud = [p["case_id"] for p in prior_cases if p.get("outcome") == "confirmed_fraud"]
    other_fraud = d.verdict == "fraud" and bool(d.connected_card_ids) and bool(connected_fraud)
    q = f"(card_id={card_id}, as_of={as_of:%Y-%m-%d %H:%M:%S})"
    dataset = getattr(src, "label", DATASET)

    evidence = [_ev(h.description, f"detector:{h.key}{q} on {dataset}",
                    [*h.txn_ids, *h.connected_card_ids[:20]]) for h in d.hits]
    evidence += [_ev(f"Not explained by the card's history — {u}.", f"anomalies{q} on {dataset}", [flagged_txn_id]) for u in d.unexplained]
    evidence += [_ev(f"Explained by the card's history: {e}.", f"explain{q} on {dataset}", [flagged_txn_id]) for e in d.explanations]
    if d.adjudication and d.adjudication.accepted:
        a = d.adjudication
        evidence.append(_ev(f"Decision model ({a.model}) assessed {a.verdict} at {a.fraud_probability:.2f}: {a.rationale}",
                            f"adjudicator(model={a.model}, case={case_id})", list(a.cited_txn_ids) or [flagged_txn_id],
                            source="external"))
    if d.vector_status and d.vector_status != "ok":
        # Never silent: precedent that should have come from vector search did not.
        evidence.append(_ev(f"Vector precedent was not used for this case ({d.vector_status}); precedent came from "
                            "graph links only.", f"vector_search(case={case_id})", [flagged_txn_id]))
    if d.injection_spans:
        evidence.append(_ev("The trigger text contains instruction-like wording aimed at an automated system "
                            f"({'; '.join(sorted(set(d.injection_spans))[:3])}); it was treated as data and did not "
                            "change the assessment.", f"scan_injection(case={case_id})", [flagged_txn_id], source="customer"))
    if not evidence:
        evidence.append(_ev("Nothing on the flagged transaction departs from the card's own history.",
                            f"anomalies{q} on {dataset}", [flagged_txn_id]))

    initial_state = _state(d, trigger, exposure, other_fraud)
    initial, _, _ = _recs(initial_state)

    # One evidence round, with a stated assumption (replies are simulated).
    if trigger == "customer_report" and d.verdict == "fraud":
        requests = []
        final_state = initial_state
    else:
        if d.verdict == "fraud":
            resp, said = CustomerResponse.DENIED, ("Assumed: the cardholder, contacted on their registered number, does "
                                                   "not recognise the transactions listed and still holds the card.")
        elif d.verdict == "legitimate":
            resp, said = CustomerResponse.CONFIRMED, ("Assumed: the cardholder confirms the purchase as their own; this "
                                                      "matches what the card's history already shows.")
        else:
            resp, said = CustomerResponse.NO_REPLY, ("Assumed: no reply from the cardholder within 24 hours. The evidence "
                                                     "supports both readings, so no answer is assumed.")
        requests = [{"type": "customer_validation", "asked_after_step": 6, "assumed_response": said}]
        final_state = replace(initial_state, customer_response=resp, response_requested=True)
    final, sar_file, sar_reason = _recs(final_state)

    final_actions = {r["action"] for r in final}
    if d.verdict == "legitimate":
        status = "closed_legitimate"
    elif Action.ESCALATE_TO_ANALYST.value in final_actions or d.verdict == "uncertain":
        status = "escalated"
    elif Action.BLOCK_CARD.value in final_actions:
        status = "closed_fraud"
    else:
        status = "open"

    sar = {"file": sar_file, "reason": sar_reason, "narrative": "", "subjects": [], "total_amount_usd": 0.0,
           "activity_dates": []}
    if sar_file:
        first, last = txns[affected[0]], txns[affected[-1]]
        conn = list(d.connected_card_ids)
        # Every sentence carries the ids or rule it rests on, in brackets before its full
        # stop, so an examiner can re-query each claim (challenge 4).
        hit = next((h for h in d.hits if h.strength >= 0.8), None)
        block_route = next((r["route"] for r in final if r["action"] == "BLOCK_CARD"), "L1")
        cite = lambda text, ref: f"{text.rstrip().rstrip('.')} [{ref}]."  # noqa: E731
        ids = ", ".join(map(str, affected[:12]))
        sentences = [
            cite(f"This report concerns card {card_id} held by customer {customer_id}", f"card {card_id}; customer {customer_id}"),
            cite(f"Between {first.ts:%Y-%m-%d} and {last.ts:%Y-%m-%d}, {len(affected)} transaction(s) totalling "
                 f"${exposure:,.2f} were identified as fraudulent", f"txn {ids}"),
            cite(f"The activity follows a pattern not covered by the bank's documented typologies: {d.pattern_description}"
                 if d.pattern == "undocumented" else f"The activity matches the documented pattern {d.pattern}",
                 f"detector {hit.key}, txn {', '.join(map(str, hit.txn_ids[:8]))}" if hit else f"decision {d.decided_by}"),
            cite(f"The same activity links to {len(conn)} other card(s) through {initial_state.shared_origin_kind}, "
                 f"including {', '.join(conn[:6])}" if conn else
                 "No other card was found sharing a device profile, recipient or burst with this activity",
                 (f"shared {hit.shared}; cards {', '.join(conn[:6])}" if hit and conn else f"card {card_id}")),
            cite(f"The first suspicious transaction was {affected[0]} on {first.ts:%Y-%m-%d %H:%M}", f"txn {affected[0]}"),
            cite(f"Policy section 3a requires this filing because "
                 f"{sar_reason[len('3a: fraud confirmed or strongly suspected and '):].rstrip('.') or 'its conditions are met'}",
                 "Fraud Policy v1.0, section 3a"),
            cite("The card has been recommended for blocking and connected cards for monitoring",
                 f"BLOCK_CARD {block_route}, MONITOR_CONNECTED_CARDS auto, awaiting approval"),
            cite("All transaction identifiers cited above exist in the bank's records and can be re-queried",
                 f"{dataset}; ty_card_txns(card_id={card_id}, until={as_of:%Y-%m-%d %H:%M:%S})"),
        ]
        sar.update(narrative=" ".join(s.strip() for s in sentences), subjects=[customer_id, card_id, *conn[:10]],
                   total_amount_usd=exposure, activity_dates=[f"{first.ts:%Y-%m-%d}", f"{last.ts:%Y-%m-%d}"])

    if d.verdict == "fraud":
        summary = (f"Fraud ({d.pattern}) at {d.fraud_probability:.2f}, decided by {d.decided_by}. "
                   + (d.pattern_description or (d.reasons[0] if d.reasons else "")))
    elif d.verdict == "legitimate":
        summary = "Legitimate: " + "; ".join(d.explanations or d.reasons)
    else:
        summary = "Uncertain and escalated: " + "; ".join(d.reasons[:2])
    answer = {
        "case_id": case_id,
        "case": {
            "status": status, "verdict": d.verdict, "fraud_probability": d.fraud_probability,
            "pattern": d.pattern if d.verdict == "fraud" else "none",
            "pattern_description": d.pattern_description if d.verdict == "fraud" and d.pattern == "undocumented" else "",
            "affected_txn_ids": [str(i) for i in affected],
            "first_suspicious_txn_id": str(affected[0]) if affected else "",
            "connected_card_ids": list(d.connected_card_ids) if d.verdict == "fraud" else [],
            "connected_device_profiles": [d.shared] if d.verdict == "fraud" and d.shared.startswith("D") and len(d.shared) == 13 else [],
            "exposure_usd": exposure,
            "evidence": evidence,
            # GraphRAG-fused precedent when it ran (graph link + vector), else the card's own history.
            "similar_prior_cases": ([p["case_id"] for p in d.precedent if str(p["case_id"]).startswith("CC-")][:6]
                                    or [p["case_id"] for p in prior_cases if str(p["case_id"]).startswith("CC-")][-6:]),
            "summary": summary[:1200],
            "written_to_graph": False, "graph_case_id": "",
        },
        "evidence_requests": requests,
        "next_best_actions": {
            "initial": initial, "final": final,
            "what_changed": "nothing" if initial == final else
            f"after the evidence request ({requests[0]['assumed_response'].split(':')[0].lower()} reply: "
            f"{final_state.customer_response.value}), the recommendation moved from "
            f"{[r['action'] for r in initial]} to {[r['action'] for r in final]}",
        },
        "sar": sar,
        "stop_reason": f"Stopped once the decision was {d.verdict} ({d.decided_by}); {sar_reason}",
        "tool_calls": tool_calls,
        # Every model token this case spent: the decision model and the GraphRAG query embedding.
        "tokens": (d.adjudication.tokens if d.adjudication else 0) + d.embed_tokens,
        "latency_s": round(latency_s, 2),
    }
    AnswerFile.model_validate(answer)  # every structural invariant, before anything is written
    return answer
