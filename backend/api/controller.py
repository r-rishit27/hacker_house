"""The class-based controller base, and what every controller takes from a request.

FastAPI's decorators are module-level functions and this build is class-based,
so a controller declares its routes as data and binds its own *bound methods*
through ``router.add_api_route``. ``fastapi-utils``' ``@cbv`` was rejected in
LLD §10 — it lags FastAPI releases and injects attributes by magic — and all it
would have bought is the thirty lines below.

Two things live here rather than in each of the seven controllers.

The container arrives **late**. It is built inside the lifespan, on the loop
that will use it, because an ``httpx.AsyncClient`` and a SQLAlchemy engine
created on one loop and awaited on another fail at the first call. A controller
therefore holds a :class:`~api.container.ContainerHandle` and resolves it per
request, which also gives the honest answer — 503, not 500 — to a request that
arrives while the process is still starting.

And the principal is parsed the same way everywhere: the ``X-Sentinel-Role``
header, then the ``?role=`` fallback the browser's ``EventSource`` forces on us
(ADR-5), then the configured default.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar

from fastapi import APIRouter, Request
from starlette.responses import Response

from api.errors import ApiError
from api import auth
from api.principal import IDEMPOTENCY_HEADER, ROLE_HEADER, Principal, parse_role
from api.schemas import Problem

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from api.container import Container, ContainerHandle

#: The longest idempotency key the store's column holds. A longer one is
#: refused rather than truncated: two different requests trimmed to the same
#: 128 characters would replay each other's response.
MAX_IDEMPOTENCY_KEY = 128


def route(
    path: str,
    endpoint: Callable[..., Any],
    *,
    methods: Sequence[str] = ("GET",),
    status_code: int | None = None,
    summary: str = "",
    response_class: type[Response] | None = None,
    errors: Mapping[int, str] | None = None,
) -> dict[str, Any]:
    """One row of a controller's route table, as ``add_api_route`` kwargs.

    ``response_model`` is deliberately absent: FastAPI infers it from the
    handler's return annotation, so the declared type and the serialised body
    cannot drift. ``errors`` documents the deliberate failures in the OpenAPI
    schema with the problem envelope they actually return.
    """
    spec: dict[str, Any] = {
        "path": path,
        "endpoint": endpoint,
        "methods": list(methods),
    }
    if status_code is not None:
        spec["status_code"] = status_code
    if summary:
        spec["summary"] = summary
    if response_class is not None:
        spec["response_class"] = response_class
    if errors:
        spec["responses"] = {
            status: {"description": description, "model": Problem}
            for status, description in errors.items()
        }
    return spec


class RouterController(ABC):
    """One resource's HTTP surface, as a class.

    Responsibility: declare its routes and bind them to an ``APIRouter``. It
    performs no I/O of its own — it resolves the container, calls a service and
    shapes the result into a model from ``api.schemas``.
    Collaborators: ``Container`` through ``ContainerHandle``; the services,
    which hold every decision a controller might otherwise be tempted to make.
    """

    #: Mounted under ``/api`` by the app factory, so ``/cases`` here is
    #: ``/api/cases`` on the wire.
    prefix: ClassVar[str] = ""
    tags: ClassVar[list[str]] = []

    @abstractmethod
    def routes(self) -> list[dict[str, Any]]:
        """This controller's route table, in the order it should be matched."""

    def build(self) -> APIRouter:
        """Bind every route to this instance's bound methods."""
        router = APIRouter(prefix=self.prefix, tags=list(self.tags))
        for spec in self.routes():
            router.add_api_route(**spec)
        return router


class ApiController(RouterController):
    """A controller with the container behind it and a request in front.

    Responsibility: resolve the late-bound container, and read the three things
    every endpoint takes off a request — who is acting, which request this is,
    and whether the caller made it replayable.
    Collaborators: ``ContainerHandle``, filled once by the lifespan.
    """

    def __init__(self, handle: ContainerHandle) -> None:
        self._handle = handle

    @property
    def container(self) -> Container:
        """The built container, or a 503 while the process is still starting."""
        return self._handle.require()

    def principal(self, request: Request) -> Principal:
        """Who is acting, from the header or the query fallback.

        The query fallback exists because the native ``EventSource`` cannot
        send a header and would otherwise stream as somebody else. It widens
        nothing: the role decides only who may *approve*, and every route is
        recomputed server-side on both the read and the execute (ADR-6).
        """
        try:
            return auth.resolve_principal(request.headers, request.query_params,
                                          self.container.settings.default_role if not auth.secret() else "",
                                          cookies=request.cookies)
        except auth.AuthError as exc:
            raise ApiError(f"invalid or missing token: {exc}", code="unauthenticated", status=401) from exc

    @staticmethod
    def request_id(request: Request) -> str:
        """The id the middleware minted, for the audit row and the problem body."""
        return str(getattr(request.state, "request_id", ""))

    @staticmethod
    def idempotency_key(request: Request) -> str | None:
        """``Idempotency-Key``, or ``None`` when the caller did not send one."""
        raw = (request.headers.get(IDEMPOTENCY_HEADER) or "").strip()
        if not raw:
            return None
        if len(raw) > MAX_IDEMPOTENCY_KEY:
            raise ApiError(
                f"{IDEMPOTENCY_HEADER} is {len(raw)} characters; the limit is "
                f"{MAX_IDEMPOTENCY_KEY}",
                code="idempotency_key_too_long",
                status=422,
            )
        return raw
