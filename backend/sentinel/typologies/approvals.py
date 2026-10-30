"""The approval chain: L1/L2 actions wait for a human whose role the server checks.

The runner records every ``BLOCK_CARD`` / ``DECLINE_TRANSACTION`` (L1) and
``FILE_REPORT`` / ``BLOCK_ALL_CARDS`` (L2) it recommends as ``approval.requested``
in the audit ledger. Only a signed identity (api/auth.py) can decide one, and
only if its role is on the route's approver list (``APPROVERS``). A refused
attempt is itself recorded — an examiner wants to see that someone tried. When
nothing is left pending the case's status changes, and that is recorded too.

    python -m sentinel.typologies.approvals --ledger ../audit/ledger.jsonl pending --case HHF-007
    python -m sentinel.typologies.approvals --ledger ../audit/ledger.jsonl decide --case HHF-007 \\
        --action FILE_REPORT --token <sv1...> [--reject] [--note "..."]
"""

from __future__ import annotations

import argparse

from sentinel.domain.enums import APPROVERS, Route
from sentinel.typologies.audit import Ledger, default_ledger


class ApprovalError(PermissionError):
    pass


def pending(ledger: Ledger, case_id: str) -> list[dict]:
    asked, done = [], set()
    for e in ledger.entries(case_id):
        if e["kind"] == "approval.requested":
            asked.append(e)
        elif e["kind"] in ("approval.granted", "approval.rejected"):
            done.add(e["data"]["action"])
    return [e["data"] | {"requested_seq": e["seq"]} for e in asked if e["data"]["action"] not in done]


def decide(ledger: Ledger, case_id: str, action: str, token: str, *, approve: bool = True, note: str = "") -> dict:
    from api import auth

    try:
        claims = auth.verify(token)
    except auth.AuthError as exc:
        ledger.append(case_id, "approval.denied", {"action": action, "why": f"invalid token: {exc}"},
                      actor="unknown", role="unauthenticated")
        raise ApprovalError(f"invalid token: {exc}") from exc
    req = next((p for p in pending(ledger, case_id) if p["action"] == action), None)
    if req is None:
        raise ApprovalError(f"{action} on {case_id} is not awaiting approval")
    route = Route(req["route"])
    if claims.role not in APPROVERS[route]:
        ledger.append(case_id, "approval.denied", {
            "action": action, "route": route.value, "why": f"role {claims.role.value} may not approve {route.value}",
            "approvers": [r.value for r in APPROVERS[route]]}, actor=claims.sub, role=claims.role.value)
        raise ApprovalError(f"{claims.sub} ({claims.role.value}) may not approve a {route.value} action; "
                            f"needs {[r.value for r in APPROVERS[route]]}")
    entry = ledger.append(case_id, "approval.granted" if approve else "approval.rejected", {
        "action": action, "route": route.value, "requested_seq": req["requested_seq"], "note": note},
        actor=claims.sub, role=claims.role.value)
    if not pending(ledger, case_id):
        decided = [e for e in ledger.entries(case_id) if e["kind"] in ("approval.granted", "approval.rejected")]
        to = "actions_approved" if all(e["kind"] == "approval.granted" for e in decided) else "actions_partly_rejected"
        ledger.append(case_id, "status.changed", {"to": to, "after_seq": entry["seq"]},
                      actor=claims.sub, role=claims.role.value)
    return entry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pending")
    p.add_argument("--case", required=True)
    d = sub.add_parser("decide")
    d.add_argument("--case", required=True)
    d.add_argument("--action", required=True)
    d.add_argument("--token", required=True)
    d.add_argument("--reject", action="store_true")
    d.add_argument("--note", default="")
    a = ap.parse_args()
    led = Ledger(a.ledger) if a.ledger else default_ledger()
    if a.cmd == "pending":
        for x in pending(led, a.case):
            print(f"  {x['action']:<20} {x['route']}  approvers={x['approvers']}  (request #{x['requested_seq']})")
    else:
        try:
            e = decide(led, a.case, a.action, a.token, approve=not a.reject, note=a.note)
            print(f"{e['kind']} #{e['seq']} by {e['actor']} ({e['role']})")
        except ApprovalError as exc:
            raise SystemExit(f"refused: {exc}") from exc


if __name__ == "__main__":
    main()
