"""Signed identities: the server decides who is acting, not the request.

Round 1's console read the approver's role from an ``X-Sentinel-Role`` header —
a check, not enforcement, since any caller could claim ``fraud_manager``. With
``SENTINEL_AUTH_SECRET`` set, every request must instead carry a bearer token
the server itself signed (HMAC-SHA256 over the claims ``sub``, ``role``, ``iat``,
``exp``). The role is read from the verified token and nowhere else; a role
header is ignored, a tampered or expired token is a 401.

Tokens are issued out of band by whoever administers the bank's users:

    python -m api.auth mint --user priya --role team_lead --hours 12

Without a secret the API stays in the old demo mode and says so in every
principal (``authenticated=False``), so a demo can never be mistaken for the
enforced configuration.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

from sentinel.domain.enums import Role

SECRET_ENV = "SENTINEL_AUTH_SECRET"
PREFIX = "sv1"
#: Tokens older than this are refused even if their own exp says otherwise.
MAX_LIFETIME_S = 7 * 24 * 3600


class AuthError(ValueError):
    """The token is missing, malformed, tampered with, or expired."""


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def secret() -> bytes | None:
    s = os.getenv(SECRET_ENV, "")
    return s.encode() if s else None


def mint(user: str, role: Role | str, *, hours: float = 12, key: bytes | None = None, now: float | None = None) -> str:
    key = key or secret()
    if not key:
        raise AuthError(f"{SECRET_ENV} is not set; cannot sign")
    role = Role(role)
    iat = int(now if now is not None else time.time())
    exp = iat + int(min(hours * 3600, MAX_LIFETIME_S))
    body = _b64(json.dumps({"sub": user, "role": role.value, "iat": iat, "exp": exp}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(key, f"{PREFIX}.{body}".encode(), hashlib.sha256).digest())
    return f"{PREFIX}.{body}.{sig}"


@dataclass(frozen=True, slots=True)
class Claims:
    sub: str
    role: Role
    iat: int
    exp: int


def verify(token: str, *, key: bytes | None = None, now: float | None = None) -> Claims:
    key = key or secret()
    if not key:
        raise AuthError(f"{SECRET_ENV} is not set; cannot verify")
    parts = (token or "").strip().split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise AuthError("malformed token")
    expected = _b64(hmac.new(key, f"{PREFIX}.{parts[1]}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, parts[2]):
        raise AuthError("bad signature")
    try:
        c = json.loads(_unb64(parts[1]))
        claims = Claims(str(c["sub"]), Role(c["role"]), int(c["iat"]), int(c["exp"]))
    except Exception as exc:  # noqa: BLE001
        raise AuthError("unreadable claims") from exc
    t = now if now is not None else time.time()
    if t >= claims.exp or claims.exp - claims.iat > MAX_LIFETIME_S:
        raise AuthError("token expired")
    if not claims.sub:
        raise AuthError("token has no subject")
    return claims


def bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


#: The HttpOnly session cookie the v2 sign-in sets (api_v2/app.py). Same token, same secret.
SESSION_COOKIE = "sentinel_session"


def resolve_principal(headers, query, default_role: str, cookies=None):
    """Who is acting, for one request. The whole authentication decision, in one
    pure function so it is tested exactly as the server runs it.

    Enforced (``SENTINEL_AUTH_SECRET`` set): a verified bearer token, ``?token=``
    for EventSource, or the signed session cookie from a browser sign-in; the role is the token's, a role header is
    ignored, anything else raises :class:`AuthError` (the API answers 401).
    Demo (no secret): the role header, marked ``authenticated=False``.
    """
    from api.principal import ROLE_HEADER, Principal, parse_role

    if secret():
        token = bearer(headers.get("Authorization")) or query.get("token") or (cookies or {}).get(SESSION_COOKIE)
        if not token:
            raise AuthError("authentication required: send a bearer token")
        claims = verify(token)
        return Principal(role=claims.role, actor_id=claims.sub, authenticated=True)
    return Principal(role=parse_role(headers.get(ROLE_HEADER) or query.get("role"), default_role))


def main() -> None:
    ap = argparse.ArgumentParser(description="Issue a signed Sentinel identity")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mint")
    m.add_argument("--user", required=True)
    m.add_argument("--role", required=True, choices=[r.value for r in Role])
    m.add_argument("--hours", type=float, default=12)
    a = ap.parse_args()
    print(mint(a.user, a.role, hours=a.hours))


if __name__ == "__main__":
    main()
