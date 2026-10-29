"""The audit trail: every step of every investigation, hash-chained.

An examiner asks months later why the agent concluded what it did. Each
investigation writes an append-only JSONL ledger: what the agent saw at each
step (every graph query with its parameters and a digest of what came back),
what each detector and the decision model returned, what the policy engine
recommended before and after the evidence request, what was written to the
graph, and every approval and status change afterwards — who, with what role,
when.

Each entry carries the SHA-256 of the one before it. Altering, removing or
re-ordering any past entry breaks every hash after it, and :func:`verify`
names the first broken one. Approvals are therefore not editable afterwards:
they can only be followed by later entries.

The regulator's view (``api_v2``) renders this file; nothing else feeds it.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path

GENESIS = "0" * 64
_LOCK = threading.Lock()


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def digest(obj) -> str:
    return hashlib.sha256(_canon(obj).encode()).hexdigest()[:16]


def code_version() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).resolve().parents[3], timeout=5).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


class Ledger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._last = self._tail()

    def _tail(self) -> tuple[int, str]:
        if not self.path.exists():
            return 0, GENESIS
        seq, h = 0, GENESIS
        with open(self.path, encoding="utf8") as fh:
            for line in fh:
                if line.strip():
                    e = json.loads(line)
                    seq, h = e["seq"], e["hash"]
        return seq, h

    def append(self, case_id: str, kind: str, data: dict, *, actor: str = "sentinel-agent", role: str = "system") -> dict:
        with _LOCK:
            seq, prev = self._last
            entry = {"seq": seq + 1, "at": datetime.now().isoformat(timespec="milliseconds"), "case_id": case_id,
                     "kind": kind, "actor": actor, "role": role, "data": data, "prev_hash": prev}
            entry["hash"] = hashlib.sha256((prev + _canon({k: v for k, v in entry.items() if k != "hash"})).encode()).hexdigest()
            with open(self.path, "a", encoding="utf8") as fh:
                fh.write(_canon(entry) + "\n")
            self._last = (entry["seq"], entry["hash"])
            return entry

    def entries(self, case_id: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        out = [json.loads(line) for line in open(self.path, encoding="utf8") if line.strip()]
        return [e for e in out if case_id is None or e["case_id"] == case_id]


def verify(path: str | Path) -> dict:
    """Walk the chain. Returns {"ok", "entries", "first_broken"} — the seq of the first
    entry whose hash or back-link does not match, if any."""
    prev, n = GENESIS, 0
    p = Path(path)
    if not p.exists():
        return {"ok": True, "entries": 0, "first_broken": None}
    for line in open(p, encoding="utf8"):
        if not line.strip():
            continue
        e = json.loads(line)
        n += 1
        want = hashlib.sha256((prev + _canon({k: v for k, v in e.items() if k != "hash"})).encode()).hexdigest()
        if e.get("prev_hash") != prev or e.get("hash") != want:
            return {"ok": False, "entries": n, "first_broken": e.get("seq"), "case_id": e.get("case_id")}
        prev = e["hash"]
    return {"ok": True, "entries": n, "first_broken": None}


def default_ledger() -> Ledger:
    return Ledger(os.getenv("SENTINEL_AUDIT_LEDGER", str(Path(__file__).resolve().parents[3] / "audit" / "ledger.jsonl")))


class QueryRecorder:
    """Attached to a TxnSource: every query becomes an audit entry with its parameters
    and a digest of the rows that came back, so each step is challengeable."""

    def __init__(self, ledger: Ledger, case_id: str) -> None:
        self.ledger, self.case_id = ledger, case_id

    def __call__(self, name: str, params: dict, rows: list) -> None:
        ids = [getattr(r, "txn_id", None) or (r.get("case_id") if isinstance(r, dict) else None) for r in rows[:25]]
        self.ledger.append(self.case_id, "step.query", {
            "query": name, "params": {k: (list(v) if isinstance(v, (list, tuple)) else v) for k, v in params.items()},
            "n_rows": len(rows), "first_ids": [i for i in ids if i is not None], "result_digest": digest(
                [r.__dict__ if hasattr(r, "__dict__") else r for r in rows] if rows and not hasattr(rows[0], "__slots__")
                else [str(r) for r in rows])})
