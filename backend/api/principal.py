"""Who is acting.

With ``SENTINEL_AUTH_SECRET`` set, identity is a server-signed bearer token
(api/auth.py) and the role is read from it alone. Without it the API runs in a
demo mode that takes the role from a header and marks every principal
``authenticated=False``. In both modes the route is recomputed from the policy
engine on every read and every execute, so no client can widen its own
permissions by sending a different route.
"""

from __future__ import annotations

from dataclasses import dataclass

from sentinel.domain.enums import APPROVERS, Role, Route

#: The header the console sets. Named so it cannot be confused with a real one.
ROLE_HEADER = "X-Sentinel-Role"

#: The header that makes a mutating call safe to retry.
IDEMPOTENCY_HEADER = "Idempotency-Key"


@dataclass(frozen=True, slots=True)
class Principal:
    """The actor behind one request."""

    role: Role
    #: The verified token subject; in demo mode, the role name.
    actor_id: str = ""
    #: True only when the role came from a token the server signed (api/auth.py).
    authenticated: bool = False

    @property
    def id(self) -> str:
        return self.actor_id or self.role.value

    def may_approve(self, route: Route) -> bool:
        """Whether this role is on the approver list for that route."""
        return self.role in APPROVERS[route]

    def as_dict(self) -> dict[str, str]:
        return {"actor_id": self.id, "actor_role": self.role.value, "authenticated": self.authenticated}


def parse_role(raw: str | None, default: str) -> Role:
    """The role from a header, falling back to the configured default.

    An unrecognised role falls back rather than 400ing: the boundary that
    matters is what `auto` may do, and an unknown role gets the least
    privileged answer either way.
    """
    for candidate in (raw, default):
        if not candidate:
            continue
        try:
            return Role(candidate.strip())
        except ValueError:
            continue
    return Role.ANALYST
