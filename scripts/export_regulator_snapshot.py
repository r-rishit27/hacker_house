"""Export the regulator's view as one self-contained, read-only page.

The live view (``uvicorn api_v2.app:app``) needs the service running. An examiner
who should not reach the bank's systems gets this instead: the same page with
the run directory's summary, every investigation, the raw hash-chained ledger
and the change record embedded. The page still recomputes every hash itself.

    python scripts/export_regulator_snapshot.py [--run eval/out/regulator_demo] [--out regulator_snapshot.html]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=str(ROOT / "eval" / "out" / "regulator_demo"))
    ap.add_argument("--out", default=str(ROOT / "eval" / "out" / "regulator_snapshot.html"))
    a = ap.parse_args()
    os.environ["SENTINEL_RUN_DIR"] = str(Path(a.run).resolve())
    os.environ.pop("SENTINEL_AUTH_SECRET", None)  # read in demo mode: the snapshot carries no identity
    from fastapi.testclient import TestClient

    from api_v2.app import app

    c = TestClient(app)
    summary = c.get("/v2/summary").json()
    snap = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "investigations": {r["case_id"]: c.get(f"/v2/investigations/{r['case_id']}").json() for r in summary["investigations"]},
        "ledger": c.get("/v2/ledger").text,
        "changes": c.get("/v2/changes").json(),
    }
    page = (ROOT / "backend" / "api_v2" / "static" / "regulator.html").read_text(encoding="utf8")
    # </ inside JSON would end the script element early; escape it.
    data = json.dumps(snap, ensure_ascii=False).replace("</", "<\\/")
    out = Path(a.out)
    # The snapshot goes just before the page's own script: the <title> stays at the top
    # of the file, and the data is defined before the script that reads it.
    marker = "<script>\n(() => {"
    if marker not in page:
        raise SystemExit("regulator.html changed shape: cannot find its main script")
    out.write_text(page.replace(marker, f"<script>window.__SNAPSHOT__ = {data};</script>\n" + marker, 1), encoding="utf8")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB): {len(snap['investigations'])} investigations, "
          f"{snap['ledger'].count(chr(10))} ledger entries, chain {summary['chain']}")


if __name__ == "__main__":
    main()
