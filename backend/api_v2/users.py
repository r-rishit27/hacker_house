"""People who can sign in: username, salted password hash, role.

Passwords are never stored: each one is hashed with PBKDF2-HMAC-SHA256
(600,000 iterations, a fresh 16-byte salt per user) and compared in constant
time. Five wrong passwords in a row lock the account for 15 minutes; every
sign-in, failure, lock and account change is written to the audit ledger by the
service. The store is a JSON file outside version control
(``SENTINEL_USERS_FILE``, default backend/var/users.json).

    python -m api_v2.users add --user priya --role team_lead          # asks for the password
    python -m api_v2.users list
    python -m api_v2.users disable --user priya
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from sentinel.domain.enums import Role

ITERATIONS = 600_000
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
MIN_PASSWORD = 10
USERS_FILE = Path(os.getenv("SENTINEL_USERS_FILE", str(Path(__file__).resolve().parents[1] / "var" / "users.json")))


class LoginError(ValueError):
    pass


def _hash(password: str, salt: bytes, iterations: int = ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations).hex()


def _load(path: Path = USERS_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf8")) if path.exists() else {}


def _save(users: dict, path: Path = USERS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(users, indent=1), encoding="utf8")
    tmp.replace(path)


def add(username: str, password: str, role: Role | str, *, name: str = "", path: Path = USERS_FILE) -> dict:
    username = username.strip().lower()
    if not username or not username.replace(".", "").replace("_", "").replace("-", "").isalnum():
        raise LoginError("username may use letters, digits, '.', '_' and '-'")
    if len(password) < MIN_PASSWORD:
        raise LoginError(f"password must be at least {MIN_PASSWORD} characters")
    users = _load(path)
    salt = secrets.token_bytes(16)
    users[username] = {"role": Role(role).value, "name": name or username, "salt": salt.hex(),
                       "hash": _hash(password, salt), "iterations": ITERATIONS, "disabled": False,
                       "failures": 0, "locked_until": 0, "created_at": int(time.time())}
    _save(users, path)
    return public(username, users[username])


def public(username: str, u: dict) -> dict:
    return {"username": username, "name": u.get("name", username), "role": u["role"], "disabled": u.get("disabled", False),
            "locked": u.get("locked_until", 0) > time.time()}


def authenticate(username: str, password: str, *, path: Path = USERS_FILE) -> dict:
    """Return the user's public record, or raise LoginError with the reason to log.

    The message shown to the person is always the same ("username or password is
    wrong") so the endpoint does not reveal which usernames exist."""
    users = _load(path)
    key = (username or "").strip().lower()
    u = users.get(key)
    if u is None:
        _hash(password or "", b"\0" * 16)  # same work as a real check, so timing does not reveal the miss
        raise LoginError("unknown user")
    if u.get("disabled"):
        raise LoginError("account disabled")
    if u.get("locked_until", 0) > time.time():
        raise LoginError("account locked after repeated failures")
    ok = hmac.compare_digest(_hash(password or "", bytes.fromhex(u["salt"]), u.get("iterations", ITERATIONS)), u["hash"])
    if not ok:
        u["failures"] = u.get("failures", 0) + 1
        if u["failures"] >= MAX_FAILURES:
            u["locked_until"], u["failures"] = time.time() + LOCK_SECONDS, 0
        _save(users, path)
        raise LoginError("wrong password" + (" — account now locked" if u.get("locked_until", 0) > time.time() else ""))
    if u.get("failures"):
        u["failures"] = 0
        _save(users, path)
    return public(key, u)


def disable(username: str, *, path: Path = USERS_FILE) -> None:
    users = _load(path)
    users[username.lower()]["disabled"] = True
    _save(users, path)


def all_users(*, path: Path = USERS_FILE) -> list[dict]:
    return [public(k, v) for k, v in sorted(_load(path).items())]


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--user", required=True)
    a.add_argument("--role", required=True, choices=[r.value for r in Role])
    a.add_argument("--name", default="")
    a.add_argument("--password-env", help="read the password from this environment variable instead of asking")
    sub.add_parser("list")
    d = sub.add_parser("disable")
    d.add_argument("--user", required=True)
    x = ap.parse_args()
    if x.cmd == "add":
        pw = os.environ[x.password_env] if x.password_env else getpass.getpass(f"password for {x.user}: ")
        print(add(x.user, pw, x.role, name=x.name))
    elif x.cmd == "list":
        for u in all_users():
            print(u)
    else:
        disable(x.user)
        print(f"disabled {x.user}")


if __name__ == "__main__":
    main()
