"""Signed identities: the role comes from a token the server signed, or the request fails."""

from __future__ import annotations

import pytest

from api import auth
from sentinel.domain.enums import Role, Route

KEY = b"test-secret-not-for-production"


def test_round_trip():
    c = auth.verify(auth.mint("priya", "team_lead", key=KEY), key=KEY)
    assert c.sub == "priya" and c.role is Role.TEAM_LEAD


def test_tampered_role_is_rejected():
    tok = auth.mint("raj", "analyst", key=KEY)
    head, body, sig = tok.split(".")
    forged = auth._b64(auth._unb64(body).replace(b'"analyst"', b'"fraud_manager"'))
    with pytest.raises(auth.AuthError, match="signature"):
        auth.verify(f"{head}.{forged}.{sig}", key=KEY)


def test_other_key_is_rejected():
    with pytest.raises(auth.AuthError):
        auth.verify(auth.mint("raj", "analyst", key=b"another"), key=KEY)


def test_expired_is_rejected():
    tok = auth.mint("raj", "analyst", hours=1, key=KEY, now=1_000_000)
    with pytest.raises(auth.AuthError, match="expired"):
        auth.verify(tok, key=KEY, now=1_000_000 + 3601)


@pytest.mark.parametrize("bad", ["", "garbage", "sv1.only-two", "jwt.a.b"])
def test_malformed_is_rejected(bad):
    with pytest.raises(auth.AuthError):
        auth.verify(bad, key=KEY)


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setenv(auth.SECRET_ENV, KEY.decode())


def resolve(headers=None, query=None):
    return auth.resolve_principal(headers or {}, query or {}, "analyst")


def test_enforced_mode_ignores_a_claimed_role_header(enforced):
    tok = auth.mint("raj", "analyst", key=KEY)
    p = resolve({"Authorization": f"Bearer {tok}", "X-Sentinel-Role": "fraud_manager"})
    assert p.role is Role.ANALYST and p.authenticated and p.id == "raj"
    assert not p.may_approve(Route.L1) and not p.may_approve(Route.L2)


def test_enforced_mode_requires_a_token(enforced):
    with pytest.raises(auth.AuthError, match="required"):
        resolve({"X-Sentinel-Role": "fraud_manager"})


def test_enforced_mode_rejects_a_forged_token(enforced):
    with pytest.raises(auth.AuthError):
        resolve({"Authorization": "Bearer sv1.e30.AAAA"})


def test_event_source_token_query_fallback(enforced):
    p = resolve(query={"token": auth.mint("anika", "fraud_manager", key=KEY)})
    assert p.role is Role.FRAUD_MANAGER and p.may_approve(Route.L2)


def test_demo_mode_is_marked_unauthenticated(monkeypatch):
    monkeypatch.delenv(auth.SECRET_ENV, raising=False)
    p = resolve({"X-Sentinel-Role": "team_lead"})
    assert p.role is Role.TEAM_LEAD and not p.authenticated


def test_enforced_mode_accepts_the_signed_in_session_cookie(enforced):
    # The console signs in once (api_v2) and the console API reads the same HttpOnly cookie.
    tok = auth.mint("anika", "fraud_manager", key=KEY)
    p = auth.resolve_principal({"X-Sentinel-Role": "analyst"}, {}, "", cookies={auth.SESSION_COOKIE: tok})
    assert p.role is Role.FRAUD_MANAGER and p.authenticated and p.id == "anika"
    with pytest.raises(auth.AuthError):
        auth.resolve_principal({}, {}, "", cookies={auth.SESSION_COOKIE: tok[:-3] + "xyz"})
