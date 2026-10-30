"""Wake the Savanna workspace and prove the graph answers, before a drill or a demo.

The workspace auto-stops when idle; the first request after that gets a
"Starting workspace" page for ~20 s and an investigation started then fails
closed. Run this a minute before:

    python scripts/warmup.py            # wait up to 10 minutes, then check counts and one query

Exit 0 when the graph is ready, 1 when it is not.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from etl import config as cfg  # noqa: E402

EXPECTED = {"Transaction": 650_938, "ClosedCase": 5_585, "Card": 14_317}


def main(timeout_s: int = 600) -> int:
    t0 = time.time()
    while True:
        try:
            code = httpx.get(f"{cfg.TG_HOST}/api/ping", timeout=30).status_code
        except httpx.HTTPError:
            code = 0
        print(f"  ping {code} at {time.time() - t0:.0f}s")
        if code == 200:
            break
        if time.time() - t0 > timeout_s:
            print("workspace did not start; resume it from the TigerGraph Cloud console")
            return 1
        time.sleep(15)
    conn = cfg.connect(verbose=True)
    ok = True
    for v, n in EXPECTED.items():
        got = conn.getVertexCount(v)
        ok &= got == n
        print(f"  [{'ok ' if got == n else 'FAIL'}] {v:<12} {got:>9,}")
    installed = len(conn.getInstalledQueries())
    ok &= installed >= 33
    print(f"  [{'ok ' if installed >= 33 else 'FAIL'}] installed queries {installed}")
    from sentinel.typologies.tg import TigerGraphTxnSource

    q0 = time.time()
    rows = TigerGraphTxnSource(conn).card_txns("C13487-K1", datetime(2016, 11, 23))
    print(f"  [{'ok ' if rows else 'FAIL'}] ty_card_txns returned {len(rows)} rows in {time.time() - q0:.2f}s")
    print("READY" if ok and rows else "NOT READY", f"({time.time() - t0:.0f}s)")
    return 0 if ok and rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
