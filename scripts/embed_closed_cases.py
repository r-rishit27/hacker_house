"""Embed the 5,585 closed-case analyst notes and store them on the graph.

GraphRAG precedent (sentinel/typologies/vector.py) needs ``ClosedCase.emb`` to be
filled. This embeds each case's analyst notes with ``OPENAI_EMBEDDING_MODEL``
(``OPENAI_EMBEDDING_DIMENSIONS``, default 256) and upserts the vectors onto the
ClosedCase vertices in GRAPH_GOA, in batches. Rows that already carry a vector of
the right length are skipped, so an interrupted run resumes.

    python scripts/embed_closed_cases.py            # ~2 minutes, about a cent

What leaves the bank: the analyst-note text of closed cases (pseudonymous ids,
amounts, dates; no names) goes to the OpenAI embeddings endpoint.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from openai import OpenAI  # noqa: E402

from etl import config as cfg  # noqa: E402

BATCH = 200


def fetch(conn) -> list[dict]:
    rows = []
    for lo, hi in [(f"CC-{i:04d}", f"CC-{i + 500:04d}") for i in range(0, 6000, 500)]:
        res = conn.runInstalledQuery("closed_case_range", params={"from_id": lo, "to_id": hi, "with_emb": True},
                                     timeout=300_000)
        for b in res:
            for v in b.get("cases", []):
                a = {k.split(".", 1)[-1]: x for k, x in v["attributes"].items()}
                rows.append(a)
    return rows


def main() -> None:
    model = os.environ["OPENAI_EMBEDDING_MODEL"]
    dims = int(os.getenv("OPENAI_EMBEDDING_DIMENSIONS", "256"))
    conn = cfg.connect(verbose=False)
    rows = fetch(conn)
    todo = [r for r in rows if len(r.get("emb") or []) != dims and (r.get("analyst_notes") or "").strip()]
    print(f"{len(rows)} closed cases in the graph; {len(todo)} need a {dims}-d vector ({model})")
    client = OpenAI()
    t0, tokens = time.time(), 0
    for i in range(0, len(todo), BATCH):
        chunk = todo[i: i + BATCH]
        resp = client.embeddings.create(model=model, dimensions=dims, input=[r["analyst_notes"][:4000] for r in chunk])
        tokens += resp.usage.total_tokens
        conn.upsertVertices("ClosedCase", [(r["case_id"], {"emb": d.embedding}) for r, d in zip(chunk, resp.data)])
        print(f"  {i + len(chunk):>5}/{len(todo)}  {time.time() - t0:.0f}s")
    print(f"done: {len(todo)} vectors written to ClosedCase.emb, {tokens:,} embedding tokens, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
