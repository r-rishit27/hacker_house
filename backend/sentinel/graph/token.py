"""Bearer tokens for REST++: mint once, cache, re-mint when rejected."""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Mapping
from typing import Any, Final

import httpx

from sentinel.config.settings import Settings
from sentinel.domain.errors import GraphColdStart, GraphUnavailable
from sentinel.graph.normalize import is_cold_start


class TokenManager:
    """Holds the REST++ bearer token and knows how to replace it.

    Responsibility: authentication only. It mints a token from the GSQL secret,
    caches it until shortly before it expires, and mints a new one when the
    repository reports that the workspace rejected the old one.
    Collaborators: an ``httpx.AsyncClient`` owned by the caller, and the
    ``Settings`` that carry the secret. Held by ``TigerGraphRestRepository``.

    Savanna authenticates with a secret, not a password, and every REST++ call
    wants ``Authorization: Bearer <token>``. Tokens expire, so a long-lived
    process cannot mint one at startup and forget about it.
    """

    #: Re-mint this far before the stated expiry rather than racing it.
    LEEWAY: Final[dt.timedelta] = dt.timedelta(minutes=2)

    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient,
        *,
        lifetime_s: int | None = None,
    ) -> None:
        self._settings = settings
        self._http = http
        self._lifetime_s = lifetime_s
        self._lock = asyncio.Lock()
        #: A token already in the environment is trusted until the workspace
        #: refuses it; its expiry is not in .env, so none is assumed.
        self._token: str | None = (
            settings.tg_api_token.get_secret_value() if settings.tg_api_token else None
        )
        self._expires_at: dt.datetime | None = None

    @property
    def expires_at(self) -> dt.datetime | None:
        """When the cached token dies, or ``None`` when that is not known."""
        return self._expires_at

    async def get_token(self) -> str:
        """The current token, minting one if there is none or it is about to die."""
        async with self._lock:
            if self._token is not None and not self._is_expiring():
                return self._token
            token, _ = await self._mint()
            return token

    def invalidate(self) -> None:
        """Forget the cached token after the workspace rejected it with 401/403."""
        self._token = None
        self._expires_at = None

    async def mint(self) -> tuple[str, str]:
        """Mint a fresh token from the secret. Returns ``(token, expiry)``."""
        async with self._lock:
            return await self._mint()

    # ── internals ────────────────────────────────────────────────────────────

    async def _mint(self) -> tuple[str, str]:
        """Mint and cache. The caller holds ``self._lock``."""
        secret = self._settings.tg_secret.get_secret_value()
        if not secret:
            raise GraphUnavailable("TG_SECRET is empty; no bearer token can be minted")

        # TigerGraph 4.x (Savanna today) mints at /gsql/v1/tokens and wants the graph
        # name; 3.x workspaces mint at /restpp/requesttoken from a graph-scoped secret.
        # Try the 4.x endpoint first and fall back, so either generation works.
        attempts: list[tuple[str, dict[str, Any]]] = [
            (f"{self._settings.tg_host}/gsql/v1/tokens", {"secret": secret, "graph": self._settings.tg_graph}),
            (f"{self._settings.restpp_base}/requesttoken", {"secret": secret}),
        ]
        response: httpx.Response | None = None
        url = ""
        for url, body in attempts:
            if self._lifetime_s is not None:
                body["lifetime"] = self._lifetime_s
            try:
                response = await self._http.post(
                    url, json=body, timeout=self._settings.tg_query_timeout_s
                )
            except httpx.TransportError as exc:
                raise GraphUnavailable(f"could not reach {url}: {exc}", url=url) from exc
            if is_cold_start(response.text):
                raise GraphColdStart("the workspace is waking; token not minted yet", url=url)
            if response.headers.get("content-type", "").startswith("application/json"):
                break  # this endpoint speaks the protocol; its answer, good or bad, is the one to read
        assert response is not None

        payload = self._payload(response, url)
        token, expiry = self._read_token(payload, url)
        self._token = token
        self._expires_at = self._as_datetime(expiry)
        return token, str(expiry)

    @staticmethod
    def _payload(response: httpx.Response, url: str) -> Mapping[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise GraphUnavailable(
                f"token endpoint returned {response.status_code} and no JSON",
                url=url,
                status=response.status_code,
            ) from exc
        if not isinstance(payload, Mapping):
            raise GraphUnavailable("token endpoint returned a non-object body", url=url)
        return payload

    @staticmethod
    def _read_token(payload: Mapping[str, Any], url: str) -> tuple[str, Any]:
        """Pull the token out of either response shape TigerGraph uses."""
        results = payload.get("results")
        source: Mapping[str, Any] = results if isinstance(results, Mapping) else payload
        token = source.get("token")
        if not isinstance(token, str) or not token:
            raise GraphUnavailable(
                f"token endpoint returned no token: {payload.get('message') or payload}",
                url=url,
                code=payload.get("code"),
            )
        return token, source.get("expiration", "unknown")

    @staticmethod
    def _as_datetime(expiry: Any) -> dt.datetime | None:
        """Read TigerGraph's expiry, which is epoch seconds on this deployment."""
        if isinstance(expiry, bool):
            return None
        if isinstance(expiry, (int, float)):
            return dt.datetime.fromtimestamp(float(expiry), tz=dt.UTC)
        if isinstance(expiry, str):
            try:
                return dt.datetime.fromisoformat(expiry).astimezone(dt.UTC)
            except ValueError:
                pass
            try:  # TigerGraph 4.x: "Fri Nov 06 05:26:51 UTC 2026"
                return dt.datetime.strptime(expiry, "%a %b %d %H:%M:%S UTC %Y").replace(tzinfo=dt.UTC)
            except ValueError:
                return None
        return None

    def _is_expiring(self) -> bool:
        if self._expires_at is None:
            return False
        return dt.datetime.now(tz=dt.UTC) + self.LEEWAY >= self._expires_at
