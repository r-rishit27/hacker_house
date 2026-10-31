"""From a proposal to a mapping Sentinel will use: a person's edits, then a signed approval.

    onboarding/banks/<Bank>/schema.gsql     the bank's schema, as received
    onboarding/banks/<Bank>/proposed.json   what the proposer said (never edited)
    onboarding/banks/<Bank>/review.json     a person's changes, each with a reason
    onboarding/banks/<Bank>/mapping.json    proposed + review, validated against the schema
    onboarding/banks/<Bank>/approval.json   who approved which mapping (by its hash), with a receipt

The review exists for what a schema cannot show: data values (which partyType
means a customer, which T24 TRANSACTION_TYPE codes are online), and anything the
proposer missed. Every change is recorded with who made it and why, so an
examiner can see exactly where a person overrode the proposal.

The approval follows the January data-drift approval: a team lead or fraud
manager's signed identity (api/auth.py), bound to the SHA-256 of mapping.json.
Editing the mapping afterwards voids the approval, and an unapproved mapping is
refused at run time.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path

from sentinel.onboarding import mapping as M
from sentinel.onboarding.schema import Schema

APPROVER_ROLES = {"team_lead", "fraud_manager"}


def apply(proposed: dict, review: dict) -> dict:
    """The proposal with the review's changes applied, in order. Each change replaces one item."""
    out = copy.deepcopy(proposed)
    for ch in review.get("changes", []):
        section, _, key = ch["target"].partition(":")
        if section not in ("concepts", "relations", "attributes", "defaults"):
            raise ValueError(f"review change targets unknown section {section!r}")
        out.setdefault(section, {})[key] = ch["value"]
    out["review"] = {"by": review.get("by", ""), "at": review.get("at", ""),
                     "changes": [{"target": c["target"], "why": c["why"]} for c in review.get("changes", [])]}
    return out


def build(schema: Schema, proposed: dict, review: dict) -> tuple[dict, list[str]]:
    m = apply(proposed, review)
    return m, M.validate(schema, m)


def mapping_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _receipt(mhash: str, sub: str, role: str, at: str) -> str:
    from api import auth

    return hmac.new(auth.secret() or b"", f"mapping|{mhash}|{sub}|{role}|{at}".encode(), hashlib.sha256).hexdigest()


def approve(bank_dir: str | Path, token: str) -> dict:
    from api import auth

    bank_dir = Path(bank_dir)
    claims = auth.verify(token)
    if claims.role.value not in APPROVER_ROLES:
        raise PermissionError(f"refused: {claims.sub} is {claims.role.value}; a bank mapping needs {sorted(APPROVER_ROLES)}")
    mhash = mapping_hash(bank_dir / "mapping.json")
    at = datetime.now(UTC).isoformat(timespec="seconds")
    rec = {"mapping_sha256": mhash, "approved_by": claims.sub, "role": claims.role.value, "approved_at": at,
           "receipt": _receipt(mhash, claims.sub, claims.role.value, at)}
    (bank_dir / "approval.json").write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    return rec


def approved(bank_dir: str | Path) -> tuple[bool, str]:
    from api import auth

    bank_dir = Path(bank_dir)
    a = bank_dir / "approval.json"
    if not a.exists():
        return False, "not approved: run `python -m sentinel.onboarding approve` with a team lead's or fraud manager's token"
    rec = json.loads(a.read_text(encoding="utf-8"))
    if rec.get("mapping_sha256") != mapping_hash(bank_dir / "mapping.json"):
        return False, "mapping.json changed after it was approved; approve it again"
    if rec.get("role") not in APPROVER_ROLES:
        return False, f"approved by role {rec.get('role')}, which may not approve mappings"
    if not auth.secret():
        return False, "SENTINEL_AUTH_SECRET is not set, so the approval receipt cannot be checked"
    if not hmac.compare_digest(_receipt(rec["mapping_sha256"], rec["approved_by"], rec["role"], rec["approved_at"]),
                               rec.get("receipt", "")):
        return False, "approval receipt does not verify against this server's secret"
    return True, f"approved by {rec['approved_by']} ({rec['role']}) at {rec['approved_at']}"


def load_for_use(bank_dir: str | Path, *, allow_unapproved: bool = False) -> dict:
    """The mapping, only if a person approved this exact file (or a test says it may run without)."""
    ok, why = approved(bank_dir)
    if not ok and not allow_unapproved:
        raise PermissionError(f"{Path(bank_dir).name}: {why}")
    return M.load(Path(bank_dir) / "mapping.json")
