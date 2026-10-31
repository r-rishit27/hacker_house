"""Create the three test banks as their own graphs in TigerGraph and load their sample records.

    python scripts/load_onboarding_samples.py            # create (if missing) and load all three
    python scripts/load_onboarding_samples.py --check    # counts only

Each bank is created from its schema file exactly as received (onboarding/banks/<Bank>/schema.gsql), next to
GRAPH_GOA, which is not touched. The records are the fraud story in backend/sentinel/onboarding/fixtures.py,
written in that bank's own vertex and edge names, so a live run through the bank's approved mapping can be
compared with the in-memory dry run.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from etl import config as cfg  # noqa: E402
from sentinel.onboarding import fixtures as F, schema as S  # noqa: E402


def _val(v):
    return v.strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, datetime) else v


#: Field names a bank may use that GSQL reserves. T24's CUSTOMER has TARGET. The live graph gets a trailing
#: underscore; a mapping that uses such a field must name the renamed attribute (none of the test mappings do).
GSQL_RESERVED = ("TARGET", "SOURCE", "FROM", "TO", "TYPE", "VALUES", "SELECT", "LIMIT", "ORDER", "ACCUM")


def _rename_reserved(body: str) -> tuple[str, list[str]]:
    import re

    renamed = []
    for word in GSQL_RESERVED:
        pat = re.compile(rf"([(,]\s*){word}(\s+(?:STRING|INT|UINT|DOUBLE|FLOAT|BOOL|DATETIME)\b)")
        if pat.search(body):
            body = pat.sub(rf"\g<1>{word}_\g<2>", body)
            renamed.append(f"{word} -> {word}_")
    return body, renamed


def create(admin, bank: str) -> str:
    import time

    if f"Graph {bank}(" in admin.gsql(f"SHOW GRAPH {bank}"):
        return "exists"
    text = (ROOT / "onboarding" / "banks" / bank / "schema.gsql").read_text(encoding="utf-8")
    body, renamed = _rename_reserved("\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#")))
    out = admin.gsql(body)
    if "Encountered" in out or "error" in out.lower() or "fail" in out.lower():
        raise SystemExit(f"{bank}: schema did not apply:\n{out[-1500:]}")
    for _ in range(24):  # a new graph takes a moment to appear to REST
        if f"Graph {bank}(" in admin.gsql(f"SHOW GRAPH {bank}"):
            break
        time.sleep(5)
    return "created" + (f" (GSQL reserved words renamed: {', '.join(renamed)})" if renamed else "")


def load(bank: str) -> dict:
    schema = S.load(ROOT / "onboarding" / "banks" / bank / "schema.gsql")
    data = F.BANKS[bank]()
    conn = cfg.connect(graphname=bank, verbose=False)
    for vtype, rows in data["vertices"].items():
        if rows:
            conn.upsertVertices(vtype, [(vid, {k: _val(v) for k, v in attrs.items()}) for vid, attrs in rows.items()])
    groups: dict[tuple[str, str, str], list] = {}
    for edge, ft, fid, tt, tid in data["edges"]:
        groups.setdefault((ft, edge, tt), []).append((fid, tid, {}))
    for (ft, edge, tt), rows in groups.items():
        conn.upsertEdges(ft, edge, tt, rows)
    return counts(conn, schema)


def counts(conn, schema) -> dict:
    return {v: conn.getVertexCount(v) for v in schema.vertices if conn.getVertexCount(v)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    admin = cfg.connect(verbose=False)
    for bank in F.BANKS:
        if a.check:
            print(bank, counts(cfg.connect(graphname=bank, verbose=False), S.load(ROOT / "onboarding" / "banks" / bank / "schema.gsql")))
            continue
        state = create(admin, bank)
        print(f"{bank}: graph {state}; loaded {load(bank)}", flush=True)


if __name__ == "__main__":
    main()
