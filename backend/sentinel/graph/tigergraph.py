"""REST++ over httpx: the production ``GraphRepository``."""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Any, Final, Self
from urllib.parse import quote

import httpx

from sentinel.config.settings import Settings
from sentinel.domain.errors import GraphColdStart, GraphUnavailable, QueryFailed
from sentinel.graph.normalize import ResponseNormalizer, is_cold_start
from sentinel.graph.repository import (
    EdgeUpsert,
    GraphRepository,
    UpsertResult,
    VertexUpsert,
)
from sentinel.graph.token import TokenManager


class TigerGraphRestRepository(GraphRepository):
    """Plain REST++ against the Savanna workspace.

    Responsibility: put a request on the wire, survive the three things this
    deployment actually does to a caller — sleeping, expiring the token, and
    answering HTML — and hand back a normalised dict.
    Collaborators: an injected ``httpx.AsyncClient``, a :class:`TokenManager` for
    authentication and a :class:`ResponseNormalizer` for the wire format.

    pyTigerGraph is deliberately not used. It is synchronous, it wraps
    ``VERTEX<T>`` parameters in 1-tuples that raw REST rejects, and it turns a
    cold start into an opaque exception.
    """

    #: Savanna needs about 45 seconds to wake. These four waits cover 65.
    _BACKOFF_S: Final[tuple[float, ...]] = (5.0, 10.0, 20.0, 30.0)

    #: A request that lands mid-wake must outlive the wake itself.
    _COLD_START_TIMEOUT_S: Final[float] = 90.0

    #: Graph, query, vertex and edge type names go into the URL path, so they are
    #: checked rather than trusted — nothing else in this class interpolates.
    _IDENT: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    #: What REST++ answers with when a vertex id is simply not there.
    _ABSENT_CODES: Final[frozenset[str]] = frozenset({"REST-30000"})
    _ABSENT_PHRASES: Final[tuple[str, ...]] = (
        "not a valid vertex id",
        "does not exist",
        "no vertex found",
    )

    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient,
        token: TokenManager,
        normalizer: ResponseNormalizer,
        *,
        owns_http: bool = False,
    ) -> None:
        self._settings = settings
        self._http = http
        self._token = token
        self._norm = normalizer
        self._owns_http = owns_http
        self._graph = self._checked(settings.tg_graph, "graph")
        self._gate = asyncio.Semaphore(settings.tg_max_concurrency)

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        """Composition-root convenience: build the whole stack from config.

        ``__init__`` stays the injection point; this exists so the API's startup
        and the live tests do not each rewrite the same four lines. The repository
        owns the client it makes here, and :meth:`aclose` closes it.
        """
        http = httpx.AsyncClient(timeout=settings.tg_query_timeout_s, follow_redirects=True)
        return cls(
            settings, http, TokenManager(settings, http), ResponseNormalizer(), owns_http=True
        )

    async def aclose(self) -> None:
        """Close the HTTP client, but only if this repository created it."""
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # ── the six operations ───────────────────────────────────────────────────

    async def run_query(self, name: str, params: Mapping[str, Any]) -> dict[str, Any]:
        self._checked(name, "query")
        payload = await self._request(
            "GET",
            f"/query/{self._graph}/{name}",
            params=self._encode_params(params),
        )
        return self._norm.normalize(payload.get("results"))

    async def upsert_vertex(self, vtype: str, vid: str, attrs: Mapping[str, Any]) -> int:
        self._checked(vtype, "vertex type")
        body = {"vertices": {vtype: {vid: self._wrap_attrs(attrs)}}}
        payload = await self._request("POST", f"/graph/{self._graph}", json_body=body)
        return self._accepted(payload, "accepted_vertices")

    async def upsert_edge(
        self,
        etype: str,
        src_type: str,
        src: str,
        dst_type: str,
        dst: str,
        attrs: Mapping[str, Any] | None = None,
    ) -> int:
        self._checked(etype, "edge type")
        self._checked(src_type, "vertex type")
        self._checked(dst_type, "vertex type")
        body = {
            "edges": {src_type: {src: {etype: {dst_type: {dst: self._wrap_attrs(attrs or {})}}}}}
        }
        payload = await self._request("POST", f"/graph/{self._graph}", json_body=body)
        return self._accepted(payload, "accepted_edges")

    async def upsert_batch(
        self,
        vertices: Sequence[VertexUpsert] = (),
        edges: Sequence[EdgeUpsert] = (),
    ) -> UpsertResult:
        """Every vertex and every edge in one POST.

        REST++ takes the whole nested payload at once, and a case write-back is
        one vertex and up to thirty edges. Looping would be thirty round trips
        to a workspace three hundred milliseconds away.
        """
        if not vertices and not edges:
            return UpsertResult(vertices=0, edges=0)

        v_body: dict[str, dict[str, Any]] = {}
        for vertex in vertices:
            self._checked(vertex.vtype, "vertex type")
            v_body.setdefault(vertex.vtype, {})[vertex.vid] = self._wrap_attrs(vertex.attrs)

        e_body: dict[str, Any] = {}
        for edge in edges:
            self._checked(edge.etype, "edge type")
            self._checked(edge.src_type, "vertex type")
            self._checked(edge.dst_type, "vertex type")
            by_src = e_body.setdefault(edge.src_type, {}).setdefault(edge.src, {})
            by_type = by_src.setdefault(edge.etype, {}).setdefault(edge.dst_type, {})
            by_type[edge.dst] = self._wrap_attrs(edge.attrs)

        body: dict[str, Any] = {}
        if v_body:
            body["vertices"] = v_body
        if e_body:
            body["edges"] = e_body
        payload = await self._request("POST", f"/graph/{self._graph}", json_body=body)
        return UpsertResult(
            vertices=self._accepted(payload, "accepted_vertices"),
            edges=self._accepted(payload, "accepted_edges"),
        )

    async def get_vertex(self, vtype: str, vid: str) -> dict[str, Any] | None:
        self._checked(vtype, "vertex type")
        path = f"/graph/{self._graph}/vertices/{vtype}/{quote(vid, safe='')}"
        try:
            payload = await self._request("GET", path)
        except QueryFailed as exc:
            if self._is_absence(exc):
                return None
            raise
        results = payload.get("results") or []
        if not results:
            return None
        return self._norm.sanitize(results[0])  # type: ignore[no-any-return]

    async def get_vertices(self, vtype: str, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        unique = list(dict.fromkeys(ids))
        found = await asyncio.gather(*(self.get_vertex(vtype, vid) for vid in unique))
        return {vid: row for vid, row in zip(unique, found, strict=True) if row is not None}

    async def stat_vertex_counts(self) -> dict[str, int]:
        payload = await self._request(
            "POST",
            f"/builtins/{self._graph}",
            json_body={"function": "stat_vertex_number", "type": "*"},
        )
        counts: dict[str, int] = {}
        for row in payload.get("results") or []:
            vtype, count = row.get("v_type"), row.get("count")
            if isinstance(vtype, str) and isinstance(count, int):
                counts[vtype] = count
        return counts

    # ── transport ────────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One REST++ call, through the cold start and one token replacement."""
        url = f"{self._settings.restpp_base}{path}"
        attempts = 0
        auth_retried = False
        while True:
            timeout = (
                self._settings.tg_query_timeout_s
                if attempts == 0
                else max(self._settings.tg_query_timeout_s, self._COLD_START_TIMEOUT_S)
            )
            try:
                response = await self._send(method, url, params, json_body, timeout)
            except GraphColdStart as exc:
                attempts = await self._retry_or_raise(attempts, exc)
                continue
            except httpx.TransportError as exc:
                attempts = await self._retry_or_raise(
                    attempts, GraphUnavailable(f"{type(exc).__name__}: {exc}", url=url)
                )
                continue

            if self._is_cold_start(response):
                attempts = await self._retry_or_raise(
                    attempts,
                    GraphColdStart("the workspace is still waking", url=url, attempts=attempts + 1),
                )
                continue

            if response.status_code in (401, 403) and not auth_retried:
                auth_retried = True
                self._token.invalidate()
                continue

            if response.status_code >= 500:
                attempts = await self._retry_or_raise(
                    attempts,
                    GraphUnavailable(
                        f"REST++ returned {response.status_code}",
                        url=url,
                        status=response.status_code,
                    ),
                )
                continue

            return self._payload(response, url)

    async def _send(
        self,
        method: str,
        url: str,
        params: Mapping[str, Any] | None,
        json_body: Mapping[str, Any] | None,
        timeout: float,
    ) -> httpx.Response:
        # The token is fetched outside the gate: minting is itself an HTTP call
        # and TokenManager serialises it with its own lock, so holding a permit
        # across a mint would only shrink the pool for everyone else.
        headers = {"Authorization": f"Bearer {await self._token.get_token()}"}
        async with self._gate:
            return await self._http.request(
                method, url, params=params, json=json_body, headers=headers, timeout=timeout
            )

    async def _retry_or_raise(self, attempts: int, error: GraphUnavailable) -> int:
        if attempts >= self._settings.tg_connect_retries:
            raise error
        await asyncio.sleep(self._BACKOFF_S[min(attempts, len(self._BACKOFF_S) - 1)])
        return attempts + 1

    def _is_cold_start(self, response: httpx.Response) -> bool:
        """An HTML holding page where JSON was expected means the workspace is waking."""
        if "json" in response.headers.get("content-type", "").lower():
            return False
        return is_cold_start(response.text)

    def _payload(self, response: httpx.Response, url: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise GraphUnavailable(
                f"REST++ returned {response.status_code} and a non-JSON body",
                url=url,
                status=response.status_code,
            ) from exc
        if not isinstance(payload, dict):
            raise QueryFailed("REST++ returned a non-object body", url=url)
        if payload.get("error") in (True, "true", "True"):
            raise QueryFailed(
                str(payload.get("message") or "TigerGraph reported an error"),
                url=url,
                tg_code=payload.get("code"),
                status=response.status_code,
            )
        return payload

    # ── encoding ─────────────────────────────────────────────────────────────

    def _encode_params(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Query parameters for the wire.

        ``VERTEX<T>`` parameters take a bare id here — ``c_in=C08623-K2``. The
        1-tuple wrapping in v1's tools.py is a pyTigerGraph convention and raw
        REST++ rejects it.
        """
        return {key: self._encode_value(key, value) for key, value in params.items()}

    def _encode_value(self, key: str, value: Any) -> Any:
        if value is None:
            raise ValueError(
                f"GSQL parameter {key!r} is None; every installed query requires a value"
            )
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (list, tuple, set)):
            return [self._encode_value(key, item) for item in value]
        return self._encode_scalar(value)

    @staticmethod
    def _encode_scalar(value: Any) -> Any:
        if isinstance(value, dt.datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(value, dt.date):
            return f"{value.isoformat()} 00:00:00"
        return value

    def _wrap_attrs(self, attrs: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        """REST++ wants every upserted attribute as ``{"value": ...}``."""
        wrapped: dict[str, dict[str, Any]] = {}
        for key, value in attrs.items():
            if isinstance(value, Mapping) and "value" in value:
                wrapped[key] = dict(value)
            else:
                wrapped[key] = {"value": self._encode_scalar(value)}
        return wrapped

    @staticmethod
    def _accepted(payload: Mapping[str, Any], field: str) -> int:
        total = 0
        for row in payload.get("results") or []:
            count = row.get(field)
            if isinstance(count, int):
                total += count
        return total

    @classmethod
    def _is_absence(cls, error: QueryFailed) -> bool:
        """Distinguish "that id is not in the graph" from "the lookup failed"."""
        if str(error.context.get("tg_code")) in cls._ABSENT_CODES:
            return True
        message = error.message.lower()
        return any(phrase in message for phrase in cls._ABSENT_PHRASES)

    @classmethod
    def _checked(cls, name: str, kind: str) -> str:
        if not cls._IDENT.match(name):
            raise ValueError(f"{kind} name {name!r} is not a bare identifier")
        return name
