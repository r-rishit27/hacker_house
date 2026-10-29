"""The closed vocabularies of the Fraud Policy and the answer format.

This module is the single source of truth. Today the 14 action identifiers exist
twice — in ``sentinel/policy.py`` and again in ``eval/validate.py`` — and a third
copy would appear in the frontend's TypeScript. Everything downstream, including
``frontend/src/lib/generated/contract.ts``, is generated or derived from here.

Every string value is transcribed verbatim from ``Guide.md``. Action names and
route identifiers in answer files must match these exactly; a typo scores zero.
"""

from __future__ import annotations

from enum import Enum


class Action(str, Enum):
    """The 14 policy actions. This is the complete set — nothing else is an action."""

    ALLOW_TRANSACTION = "ALLOW_TRANSACTION"
    DECLINE_TRANSACTION = "DECLINE_TRANSACTION"
    MONITOR_CARD = "MONITOR_CARD"
    MONITOR_CONNECTED_CARDS = "MONITOR_CONNECTED_CARDS"
    WARN_CUSTOMER = "WARN_CUSTOMER"
    VERIFY_WITH_CUSTOMER = "VERIFY_WITH_CUSTOMER"
    STEP_UP_AUTH = "STEP_UP_AUTH"
    BLOCK_CARD = "BLOCK_CARD"
    BLOCK_ALL_CARDS = "BLOCK_ALL_CARDS"
    GENERATE_REPORT = "GENERATE_REPORT"
    CREATE_CASE = "CREATE_CASE"
    FILE_REPORT = "FILE_REPORT"
    ESCALATE_TO_ANALYST = "ESCALATE_TO_ANALYST"
    CLOSE_NO_FRAUD = "CLOSE_NO_FRAUD"


class Route(str, Enum):
    """Approval routes. ``auto`` is the only one the agent may execute alone."""

    AUTO = "auto"
    L1 = "L1"
    L2 = "L2"


class Role(str, Enum):
    """Who is acting. Signed identities (api/auth.py) carry one of these.

    ``REGULATOR`` is an examiner: read-only, on no route's approver list, so it
    can never approve an action — not even an ``auto`` one."""

    ANALYST = "analyst"
    TEAM_LEAD = "team_lead"
    FRAUD_MANAGER = "fraud_manager"
    REGULATOR = "regulator"


class Verdict(str, Enum):
    """``uncertain`` is a valid verdict and earns full credit on ambiguous cases."""

    FRAUD = "fraud"
    LEGITIMATE = "legitimate"
    UNCERTAIN = "uncertain"


class CaseStatus(str, Enum):
    OPEN = "open"
    CLOSED_FRAUD = "closed_fraud"
    CLOSED_LEGITIMATE = "closed_legitimate"
    ESCALATED = "escalated"


class Pattern(str, Enum):
    """The five documented patterns, plus ``undocumented`` and ``none``.

    ``undocumented`` requires a non-empty ``pattern_description``; finding one is
    explicitly scored.
    """

    CARD_TESTING = "card_testing"
    CARD_NOT_PRESENT_FRAUD = "card_not_present_fraud"
    CARD_NOT_PRESENT_NEW_DEVICE = "card_not_present_new_device"
    OUT_OF_REGION_USE = "out_of_region_use"
    ACCOUNT_TAKEOVER = "account_takeover"
    UNDOCUMENTED = "undocumented"
    NONE = "none"


class EvidenceSource(str, Enum):
    """``DOCUMENT`` is GraphRAG's visible footprint in the deliverable."""

    GRAPH = "graph"
    DOCUMENT = "document"
    CUSTOMER = "customer"
    EXTERNAL = "external"


class RequestType(str, Enum):
    """The three evidence requests the agent may make without approval."""

    CUSTOMER_VALIDATION = "customer_validation"
    STEP_UP_AUTH = "step_up_auth"
    ANALYST_INFO = "analyst_info"


class TriggerType(str, Enum):
    """How an alert arrived. The benchmark is 11 / 8 / 1 in this order."""

    RISK_SCORE = "risk_score"
    CUSTOMER_REPORT = "customer_report"
    ANALYST_REQUEST = "analyst_request"


class CustomerResponse(str, Enum):
    """The outcome of a requested evidence round.

    ``NO_REPLY`` is the honest outcome for a genuinely ambiguous case and routes
    into R4 — it is not a failure to simulate.
    """

    DENIED = "denied"
    CONFIRMED = "confirmed"
    NO_REPLY = "no_reply"
    STEP_UP_PASSED = "step_up_passed"
    STEP_UP_FAILED = "step_up_failed"


#: Actions that are unconditionally ``auto``. Ten of the fourteen.
AUTO_ACTIONS: frozenset[Action] = frozenset(
    {
        Action.ALLOW_TRANSACTION,
        Action.MONITOR_CARD,
        Action.MONITOR_CONNECTED_CARDS,
        Action.WARN_CUSTOMER,
        Action.VERIFY_WITH_CUSTOMER,
        Action.STEP_UP_AUTH,
        Action.GENERATE_REPORT,
        Action.CREATE_CASE,
        Action.ESCALATE_TO_ANALYST,
        Action.CLOSE_NO_FRAUD,
    }
)

#: Every action whose route does not depend on exposure. ``BLOCK_CARD`` is the
#: only exposure-dependent action in the whole policy.
FIXED_ROUTES: dict[Action, Route] = {
    **{action: Route.AUTO for action in AUTO_ACTIONS},
    Action.DECLINE_TRANSACTION: Route.L1,
    Action.BLOCK_ALL_CARDS: Route.L2,
    Action.FILE_REPORT: Route.L2,
}

#: Which roles may approve which route.
APPROVERS: dict[Route, tuple[Role, ...]] = {
    Route.AUTO: (Role.ANALYST, Role.TEAM_LEAD, Role.FRAUD_MANAGER),
    Route.L1: (Role.TEAM_LEAD, Role.FRAUD_MANAGER),
    Route.L2: (Role.FRAUD_MANAGER,),
}
