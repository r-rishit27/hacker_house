"""Write a case into GRAPH_GOA and prove it is there before saying so.

The answer file is the submission; the ``FraudCase`` vertex is the bank's memory —
the next investigation on this card, device or ring reads it back through
:meth:`TigerGraphTxnSource.prior_cases`. ``written_to_graph`` is set only after
the installed ``case_by_id`` query returns the vertex with the same verdict and
the same affected transactions. A failed or partial write leaves it ``false``
and says why; it never reports a save that did not happen.

Every edge is upserted with ``vertexMustExist`` so a typo can never create a
phantom transaction or card. The alert itself is upserted first (Round 2 alerts
are not in the loaded data), with its two edges.
"""

from __future__ import annotations

from datetime import datetime

CASE_ID_PREFIX = "CASE-"
TS = "%Y-%m-%d %H:%M:%S"


def graph_case_id(case_id: str) -> str:
    return f"{CASE_ID_PREFIX}{case_id}"


def write_case(conn, answer: dict, row: dict, *, created_by: str, cites: list[str], devices: list[str]) -> dict:
    """Upsert the case, read it back, and return ``{"ok", "graph_case_id", "edges", "error"}``."""
    vid = graph_case_id(answer["case_id"])
    c = answer["case"]
    out = {"ok": False, "graph_case_id": vid, "edges": {}, "error": ""}
    try:
        conn.upsertVertex("Alert", row["case_id"], {
            "opened_at": row["opened_at"], "trigger_type": row["trigger_type"],
            "trigger_text": row.get("trigger_text", ""), "flagged_txn_id": str(row["flagged_txn_id"]),
            "card_id": row["card_id"], "customer_id": row["customer_id"],
            "risk_score": float(row.get("risk_score") or -1), "status": "investigated"})
        conn.upsertEdge("Alert", row["case_id"], "ALERT_ON_CARD", "Card", row["card_id"], vertexMustExist=True)
        conn.upsertEdge("Alert", row["case_id"], "ALERT_ON_TXN", "Transaction", str(row["flagged_txn_id"]),
                        vertexMustExist=True)
        nba = answer["next_best_actions"]
        conn.upsertVertex("FraudCase", vid, {
            "source_alert": row["case_id"], "customer_id": row["customer_id"], "card_id": row["card_id"],
            "status": c["status"], "verdict": c["verdict"], "fraud_probability": c["fraud_probability"],
            "pattern": c["pattern"], "pattern_description": c["pattern_description"],
            "exposure_usd": c["exposure_usd"], "n_affected_txns": len(c["affected_txn_ids"]),
            "summary": c["summary"][:2000],
            "initial_actions": "|".join(r["action"] for r in nba["initial"]),
            "final_actions": "|".join(r["action"] for r in nba["final"]),
            "sar_filed": bool(answer["sar"]["file"]), "stop_reason": answer["stop_reason"][:1000],
            # Dated at the alert, not the wall clock: investigations are judged as of
            # their alert time, so a later case must be able to read this one back.
            "created_at": row["opened_at"], "created_by": f"{created_by} @ {datetime.now().strftime(TS)}"})
        plan = [
            ("CASE_INVOLVES", "Transaction", c["affected_txn_ids"]),
            ("CASE_ON_CARD", "Card", [row["card_id"]]),
            ("CASE_CONNECTED_TO", "Card", c["connected_card_ids"]),
            ("CITES", "ClosedCase", [x for x in cites if x.startswith("CC-")]),
            ("IMPLICATES", "DeviceProfile", devices),
            ("FROM_ALERT", "Alert", [row["case_id"]]),
        ]
        for etype, dst_type, ids in plan:
            ids = sorted({str(i).strip() for i in ids if str(i).strip()})
            if ids:
                conn.upsertEdges("FraudCase", etype, dst_type, [(vid, i, {}) for i in ids], vertexMustExist=True)
            out["edges"][etype] = len(ids)
        # Read back through the installed query before claiming anything.
        res = conn.runInstalledQuery("case_by_id", params={"f_in": (vid,)})
        got = next((b["fraud_case"] for b in res if "fraud_case" in b), [])
        txns = next((b["involved_txns"] for b in res if "involved_txns" in b), [])
        if not got or got[0]["attributes"]["verdict"] != c["verdict"]:
            out["error"] = "read-back did not return the case with its verdict"
            return out
        back = sorted(str(t["v_id"]) for t in txns)
        if back != sorted(c["affected_txn_ids"]):
            out["error"] = f"read-back has {len(back)} involved transactions, the answer has {len(c['affected_txn_ids'])}"
            return out
        out["ok"] = True
    except Exception as exc:  # noqa: BLE001 — any failure means "not written", reported as such
        out["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return out
