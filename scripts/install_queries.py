"""Create and install Sentinel's GSQL queries.

    python scripts/install_queries.py               # create, then install all
    python scripts/install_queries.py --create-only # syntax check without installing
    python scripts/install_queries.py --list        # what is installed right now
    python scripts/install_queries.py --file queries/sentinel_queries_v2.gsql

Creating a query is fast and syntax-checks it. Installing compiles it and is
slow, so everything is created first and installed in a single INSTALL QUERY
pass at the end.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from etl import config as cfg  # noqa: E402
from scripts.load_to_tigergraph import read_gsql  # noqa: E402

QUERY_FILE = "queries/sentinel_queries.gsql"
QUERY_FILE_V2 = "queries/sentinel_queries_v2.gsql"


def query_names(text: str) -> list[str]:
    return re.findall(r"CREATE (?:OR REPLACE )?QUERY\s+(\w+)\s*\(", text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--create-only", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument(
        "--file",
        action="append",
        default=None,
        help="GSQL file(s) under graph/, relative. Defaults to both query files.",
    )
    args = ap.parse_args()

    conn = cfg.connect(verbose=False)

    if args.list:
        print(conn.gsql(f"USE GRAPH {cfg.TG_GRAPH}\nLS"))
        return

    files = args.file or [QUERY_FILE, QUERY_FILE_V2]
    files = [f for f in files if (cfg.GRAPH_DIR / f).exists()]
    body = "\n\n".join(read_gsql(f) for f in files)
    names = query_names(body)
    print(f"from {', '.join(files)}")
    print(f"creating {len(names)} queries: {', '.join(names)}\n")

    out = conn.gsql(f"USE GRAPH {cfg.TG_GRAPH}\n{body}")

    failed = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if "Syntax Error" in line or "Semantic Check Fails" in line or "draft query" in line:
            print(f"  {line}")
            failed.append(line)
        elif line.startswith("Successfully created queries"):
            print(f"  {line}")
    if failed:
        # The surrounding lines carry the actual reason, so show the whole reply.
        print("\n--- full GSQL output ---")
        print(out)
        raise SystemExit(f"\n{len(failed)} query problem(s); nothing was installed")

    if args.create_only:
        print("\ncreate-only: all queries parsed cleanly, not installed")
        return

    print(f"\ninstalling {len(names)} queries (this compiles them; expect a few minutes)")
    t0 = time.time()
    out = conn.gsql(f"USE GRAPH {cfg.TG_GRAPH}\nINSTALL QUERY {', '.join(names)}")
    print("\n".join("  " + l for l in out.strip().splitlines() if l.strip())[-3000:])
    print(f"\ninstalled in {(time.time() - t0) / 60:.1f} min")

    endpoints = conn.getInstalledQueries()
    installed = {k.split("/")[-1] for k in endpoints}
    missing = [n for n in names if n not in installed]
    print(f"\n{len(names) - len(missing)} of {len(names)} queries are callable")
    if missing:
        raise SystemExit(f"not installed: {', '.join(missing)}")


if __name__ == "__main__":
    main()
