"""RFC 9457 problem details, and the two the domain cannot produce on its own.

Every error the API returns is ``application/problem+json``. That is worth the
small ceremony because two of these responses are read by a human mid-demo —
the 403 that names who can approve, and the 409 that says a run is already
going — and a bare ``{"detail": "Forbidden"}`` tells that human nothing.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from sentinel.domain.enums import APPROVERS, Role, Route
from sentinel.domain.errors import SentinelError

PROBLEM_MEDIA_TYPE = "application/problem+json"
PROBLEM_BASE = "https://sentinel.local/problems"


def problem(
    *,
    code: str,
    title: str,
    status: int,
    detail: str,
    request_id: str = "",
    **extra: Any,
) -> JSONResponse:
    """One problem response, with the type URI derived from the code."""
    body: dict[str, Any] = {
        "type": f"{PROBLEM_BASE}/{code.replace('_', '-')}",
        "title": title,
        "status": status,
        "code": code,
        "detail": detail,
        **extra,
    }
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE)


class ApiError(SentinelError):
    """An error the HTTP layer raises that the domain has no opinion about."""

    def __init__(self, message: str, *, code: str, status: int, **context: Any) -> None:
        super().__init__(message, **context)
        self.code = code
        self.http_status = status
        self.title = code.replace("_", " ").capitalize()


def not_found(what: str, which: str) -> ApiError:
    return ApiError(f"{what} '{which}' does not exist", code="not_found", status=404)


def conflict(message: str, *, code: str = "conflict", **context: Any) -> ApiError:
    return ApiError(message, code=code, status=409, **context)


class PermissionDenied(SentinelError):
    """The recomputed route needs an approval this principal cannot give.

    Carries everything the console needs to render the boundary without a
    second request: the route, the roles that *could* approve, and — unless the
    caller opted out — the approval this denial just enqueued.
    """

    code = "forbidden_route"
    http_status = 403

    def __init__(
        self,
        *,
        action: str,
        required_route: Route,
        role: Role,
        exposure_usd: float,
        approval: dict[str, Any] | None = None,
    ) -> None:
        detail = (
            f"{action} routes to {required_route.value} at exposure "
            f"${exposure_usd:,.2f}. Role '{role.value}' may execute ['auto'] only."
        )
        super().__init__(
            detail,
            action=action,
            required_route=required_route.value,
            your_role=role.value,
            roles_that_can_approve=[r.value for r in APPROVERS[required_route]],
        )
        self.title = "Action requires approval"
        if approval is not None:
            self.context["approval"] = approval


def to_response(exc: SentinelError, request: Request) -> JSONResponse:
    """Any deliberate error as a problem response."""
    body = exc.to_problem()
    title = getattr(exc, "title", None) or body.get("title") or exc.code
    request_id = getattr(request.state, "request_id", "")
    return problem(
        code=exc.code,
        title=str(title),
        status=exc.http_status,
        detail=exc.message,
        request_id=request_id,
        **{k: v for k, v in body.items() if k not in {"title", "status", "code", "detail"}},
    )
