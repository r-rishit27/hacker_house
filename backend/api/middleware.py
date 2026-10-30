"""What every request passes through, and what every failure comes back as.

Three things, and a reason for each.

**A request id on everything.** Every audit row carries one and every problem
body returns one, so "the block on HHG-017 at 14:22 was refused" and the log
line that refused it are one grep apart. It is also a contextvar, so a log
written deep inside a tool call can name the request that caused it without
threading an argument through nine frames.

**One access-log line per request, as JSON.** Not for elegance: the demo runs
with a console, a `curl` and a benchmark batch hitting the same process, and
plain prose lines interleave into something nobody can read under pressure.

**Every deliberate failure becomes RFC 9457 problem details.** Two of these
responses are read by a human mid-demo — the 403 that names who may approve,
and the 409 that says a run is already going — and FastAPI's default
``{"detail": "Forbidden"}`` tells that human nothing.

The middleware is plain ASGI rather than ``BaseHTTPMiddleware`` on purpose:
``BaseHTTPMiddleware`` pumps the response body through an anyio stream, which
is exactly the wrong shape for an SSE connection that stays open for a minute
and must flush each event as it is written.
"""

from __future__ import annotations

import json
import logging
import re
import time
from contextvars import ContextVar
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.errors import problem, to_response
from api.principal import IDEMPOTENCY_HEADER, ROLE_HEADER
from api.store.models import new_id
from sentinel.config.settings import Settings
from sentinel.domain.errors import SentinelError

logger = logging.getLogger(__name__)
access_logger = logging.getLogger("api.access")

#: Echoed on every response and accepted on the way in, so a proxy or a
#: frontend that already has a correlation id keeps it.
REQUEST_ID_HEADER = "X-Request-Id"

#: An inbound id is used only if it looks like one. Anything else is a header a
#: client controls being written verbatim into the audit log.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

#: Paths whose access log would be noise: the SSE stream logs once when it is
#: opened, and health checks run every few seconds forever.
_QUIET = ("/api/health", "/api/ready")

_request_id: ContextVar[str] = ContextVar("request_id", default="")


def current_request_id() -> str:
    """The id of the request being served on this task, or ``""`` outside one."""
    return _request_id.get()


class RequestContextMiddleware:
    """Mints the request id, publishes it, and writes the access line.

    Responsibility: correlation and observability, nothing else. It does not
    catch exceptions — the exception handlers registered below do that, and a
    middleware that also swallowed them would produce two answers for one
    failure.
    Collaborators: every handler, through ``request.state.request_id``.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _inbound_id(scope) or new_id("req")
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id
        token = _request_id.set(request_id)
        started = time.perf_counter()
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            _request_id.reset(token)
            _access(scope, status, time.perf_counter() - started, request_id)


def _inbound_id(scope: Scope) -> str:
    for key, value in scope.get("headers", ()):
        if key == REQUEST_ID_HEADER.lower().encode():
            candidate = value.decode("latin-1")
            return candidate if _SAFE_ID.match(candidate) else ""
    return ""


def _access(scope: Scope, status: int, elapsed_s: float, request_id: str) -> None:
    path = str(scope.get("path", ""))
    if path in _QUIET:
        return
    line = {
        "request_id": request_id,
        "method": scope.get("method", ""),
        "path": path,
        "query": scope.get("query_string", b"").decode("latin-1"),
        "status": status,
        "ms": round(elapsed_s * 1000, 1),
    }
    access_logger.info(json.dumps(line, ensure_ascii=False))


# ── the failures ─────────────────────────────────────────────────────────────


async def sentinel_error(request: Request, exc: Exception) -> Response:
    """Every deliberate domain or API error, as problem details.

    ``SentinelError`` already carries the status, the stable code and the
    context — the approval on a 403, the budget on a 409 — so the handler adds
    only the request id.
    """
    if not isinstance(exc, SentinelError):  # pragma: no cover - registered by class
        return await unhandled(request, exc)
    if exc.http_status >= 500:
        logger.error("%s: %s", exc.code, exc.message, extra={"request_id": current_request_id()})
    return to_response(exc, request)


async def invalid_request(request: Request, exc: Exception) -> Response:
    """A body or query that does not satisfy the contract: 422, with the fields.

    FastAPI's default is a bare list under ``detail``; the console needs the
    same envelope as every other failure, and the field paths are what make a
    422 actionable rather than annoying.
    """
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    fields = [".".join(str(part) for part in error["loc"][1:]) or "body" for error in errors]
    return problem(
        code="invalid_request",
        title="Request does not satisfy the contract",
        status=422,
        detail="; ".join(
            f"{field}: {error['msg']}" for field, error in zip(fields, errors, strict=False)
        )
        or "the request could not be parsed",
        request_id=_state_request_id(request),
        errors=[
            {"field": field, "message": error["msg"], "type": error["type"]}
            for field, error in zip(fields, errors, strict=False)
        ],
    )


async def http_error(request: Request, exc: Exception) -> Response:
    """Starlette's own 404s and 405s, in the same envelope as everything else."""
    if not isinstance(exc, HTTPException):  # pragma: no cover - registered by class
        return await unhandled(request, exc)
    code = {404: "not_found", 405: "method_not_allowed", 422: "invalid_request"}.get(
        exc.status_code, "http_error"
    )
    return problem(
        code=code,
        title=str(exc.detail) if exc.detail else code.replace("_", " ").capitalize(),
        status=exc.status_code,
        detail=str(exc.detail),
        request_id=_state_request_id(request),
    )


async def unhandled(request: Request, exc: Exception) -> Response:
    """The last resort. Logged with its traceback, answered without one.

    A stack trace in an HTTP response is a security problem and a usability
    one; the request id in the body is what ties this answer to the traceback
    in the log.
    """
    logger.exception("unhandled %s", type(exc).__name__)
    return problem(
        code="internal_error",
        title="Internal error",
        status=500,
        detail=f"{type(exc).__name__}: the request id below appears beside the traceback",
        request_id=_state_request_id(request),
    )


def _state_request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or current_request_id())


# ── installation ─────────────────────────────────────────────────────────────


def install(app: FastAPI, settings: Settings) -> None:
    """Add the middleware and the handlers, in the order they must run.

    CORS is added last and therefore runs first, so a failed preflight and a
    403 both come back with the headers a browser needs to read them — a 403
    the console cannot read is a 403 nobody can act on.
    """
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Accept",
            "Content-Type",
            ROLE_HEADER,
            IDEMPOTENCY_HEADER,
            # The browser sends this itself on an EventSource reconnect, and
            # the API accepts it as a header as well as a query parameter.
            "Last-Event-ID",
            REQUEST_ID_HEADER,
        ],
        expose_headers=[REQUEST_ID_HEADER],
    )
    handlers: list[tuple[type[Exception], Any]] = [
        (SentinelError, sentinel_error),
        (RequestValidationError, invalid_request),
        (HTTPException, http_error),
        (Exception, unhandled),
    ]
    for exception, handler in handlers:
        app.add_exception_handler(exception, handler)


def json_error(status: int, code: str, detail: str) -> JSONResponse:
    """A problem body for a place that has no ``Request`` — inside a stream."""
    return problem(
        code=code,
        title=code.replace("_", " ").capitalize(),
        status=status,
        detail=detail,
        request_id=current_request_id(),
    )
