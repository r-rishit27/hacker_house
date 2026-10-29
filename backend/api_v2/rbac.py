"""Who may see and do what — one table, enforced by the server, read by the UI.

The portal hides what a role may not use, but hiding is not the control: every
endpoint calls :func:`require` before it does anything, so a request crafted by
hand gets the same 403 the UI would have prevented.

Approving an action is checked twice on purpose: ``approve.L1`` / ``approve.L2``
here decide whether the button exists, and ``APPROVERS`` in the policy decides
whether the approval is accepted (sentinel/typologies/approvals.py).
"""

from __future__ import annotations

from sentinel.domain.enums import Role

#: permission -> what it lets a person do, in the words the portal shows.
PERMISSIONS: dict[str, str] = {
    "queue.view": "See the alert queue",
    "case.view": "Open an investigation and its evidence",
    "case.investigate": "Start an investigation on an alert",
    "case.audit": "See an investigation's step-by-step audit trail",
    "approve.L1": "Approve or reject L1 actions (block card, decline)",
    "approve.L2": "Approve or reject L2 actions (file report, block all cards)",
    "audit.all": "See the audit trail across all investigations and verify the chain",
    "changes.view": "See the change record: data changes, configuration, status changes",
    "regulator.view": "Open the regulator's overview",
    "users.view": "See who has access and with which role",
}

GRANTS: dict[Role, frozenset[str]] = {
    Role.ANALYST: frozenset({"queue.view", "case.view", "case.investigate", "case.audit"}),
    Role.TEAM_LEAD: frozenset({"queue.view", "case.view", "case.investigate", "case.audit", "approve.L1"}),
    Role.FRAUD_MANAGER: frozenset({"queue.view", "case.view", "case.investigate", "case.audit", "approve.L1",
                                   "approve.L2", "audit.all", "changes.view", "regulator.view", "users.view"}),
    # The examiner reads everything that explains a decision and changes nothing.
    Role.REGULATOR: frozenset({"case.view", "case.audit", "audit.all", "changes.view", "regulator.view"}),
}

#: Where each role lands after signing in.
HOME: dict[Role, str] = {Role.ANALYST: "/portal", Role.TEAM_LEAD: "/portal", Role.FRAUD_MANAGER: "/portal",
                         Role.REGULATOR: "/regulator"}


class Forbidden(PermissionError):
    pass


def can(role: Role, permission: str) -> bool:
    return permission in GRANTS.get(role, frozenset())


def require(role: Role, permission: str) -> None:
    if permission not in PERMISSIONS:
        raise KeyError(f"unknown permission {permission}")
    if not can(role, permission):
        raise Forbidden(f"role {role.value} does not have '{permission}' ({PERMISSIONS[permission]})")


def describe(role: Role) -> list[dict]:
    return [{"permission": p, "what": w, "granted": can(role, p)} for p, w in PERMISSIONS.items()]
