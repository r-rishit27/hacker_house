"""Wire the TigerGraph MCP server to our Savanna workspace.

    python scripts/setup_mcp.py            # mint a token, write MCP env + .mcp.json
    python scripts/setup_mcp.py --refresh  # just mint a new token (they expire)
    python scripts/setup_mcp.py --check    # verify the server starts and sees the graph

tigergraph-mcp reads its own variable names from .env (TG_GRAPHNAME, TG_GS_PORT,
TG_API_TOKEN), which differ from the ones Sentinel's own code uses. Rather than
keep two files, both sets live in one .env and this script keeps the MCP half in
sync with the Sentinel half.

Savanna authenticates with a secret; the MCP server wants a bearer token, so the
token is minted from the secret here. Tokens expire, hence --refresh.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from etl import config as cfg  # noqa: E402

ENV_PATH = cfg.ROOT / ".env"
MCP_JSON = cfg.ROOT / ".mcp.json"

MANAGED_START = "# --- managed by scripts/setup_mcp.py: tigergraph-mcp reads these ---"


def mint_token() -> tuple[str, str]:
    conn = cfg.connect(verbose=False)
    tok = conn.getToken(cfg.TG_SECRET)
    token = tok[0] if isinstance(tok, (list, tuple)) else str(tok)
    expiry = str(tok[1]) if isinstance(tok, (list, tuple)) and len(tok) > 1 else "unknown"
    return token, expiry


#: The only keys this script owns. Everything else in .env is hand-written and
#: must survive a --refresh untouched.
MANAGED_KEYS = ("TG_GRAPHNAME", "TG_GS_PORT", "TG_API_TOKEN")


def strip_managed(text: str) -> str:
    """Remove the managed marker and the keys it owns — and nothing else.

    The previous implementation matched from the marker to the next line
    starting with "#", or to end of file. A .env whose managed block was
    followed by a plain `KEY=value` line and no further comment therefore lost
    every line after the marker. It deleted a live OPENAI_API_KEY that way.
    Deleting by key name cannot overreach.
    """
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == MANAGED_START:
            continue
        if any(stripped.startswith(f"{key}=") for key in MANAGED_KEYS):
            continue
        kept.append(line)
    return "\n".join(kept).rstrip()


def write_env(token: str) -> None:
    """Replace the managed block in .env, leaving the hand-written half alone."""
    text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    text = strip_managed(text)

    block = "\n".join([
        "",
        "",
        MANAGED_START,
        f"TG_GRAPHNAME={cfg.TG_GRAPH}",
        f"TG_GS_PORT={cfg.TG_GSQL_PORT}",
        f"TG_API_TOKEN={token}",
        "",
    ])
    ENV_PATH.write_text(text + block, encoding="utf-8")
    print(f"  .env updated (TG_GRAPHNAME, TG_GS_PORT, TG_API_TOKEN)")


def mcp_command() -> str:
    """The tigergraph-mcp binary, by absolute path when the venv has one.

    A bare "tigergraph-mcp" only resolves when the venv happens to be on the
    PATH of whatever process spawns the MCP client, which is not true of an
    IDE launched from the dock. The venv path is machine-local but it is not a
    credential, and an absolute path that works beats a relative one that
    needs a README note.
    """
    local = cfg.ROOT / ".venv" / "bin" / "tigergraph-mcp"
    return str(local) if local.exists() else "tigergraph-mcp"


def write_mcp_json() -> None:
    """Write .mcp.json with no host and no credentials in it.

    tigergraph-mcp loads .env from the working directory, so the committed
    config stays free of the workspace URL and the token. Both live in .env,
    which is gitignored.
    """
    config = {"mcpServers": {"tigergraph": {"command": mcp_command(),
                                            "args": [], "env": {}}}}
    MCP_JSON.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"  {MCP_JSON.name} written (host and token stay in .env)")


def _rpc(requests: list[dict], want_ids: set[int], timeout: int = 180) -> dict[int, dict]:
    """Drive the stdio server, keeping stdin OPEN until the replies arrive.

    Writing every request and closing stdin immediately makes the server shut
    down on EOF, which kills any call still in flight. tools/list survives that
    race; a real query does not.
    """
    env = dict(os.environ)
    env.update({
        "TG_HOST": cfg.TG_HOST,
        "TG_GRAPHNAME": cfg.TG_GRAPH,
        "TG_SECRET": cfg.TG_SECRET,
        "TG_RESTPP_PORT": cfg.TG_RESTPP_PORT,
        "TG_GS_PORT": cfg.TG_GSQL_PORT,
        "TG_SSL_PORT": "443",
    })
    proc = subprocess.Popen(
        ["tigergraph-mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, cwd=str(cfg.ROOT), env=env, bufsize=1,
    )
    seen: dict[int, dict] = {}
    try:
        for req in requests:
            proc.stdin.write(json.dumps(req) + "\n")
            proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline and not want_ids <= seen.keys():
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(msg.get("id"), int):
                seen[msg["id"]] = msg
    finally:
        try:
            proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
    return seen


HANDSHAKE = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "sentinel-check", "version": "1.0"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
]


def check() -> bool:
    """Confirm the server starts, exposes its tools, and can run OUR query."""
    print("  starting tigergraph-mcp over stdio")
    reqs = HANDSHAKE + [
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        # The real proof: one of Sentinel's own installed queries, whose answer
        # was verified by hand in docs/HAND_INVESTIGATION.md.
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "tigergraph__run_installed_query",
                    "arguments": {"graph_name": cfg.TG_GRAPH,
                                  "query_name": "region_novelty",
                                  "params": {"c_in": "C08623-K2", "region": "330.0",
                                             "as_of": "2016-12-10 13:01:21"}}}},
    ]
    try:
        seen = _rpc(reqs, want_ids={2, 3})
    except FileNotFoundError:
        print("  tigergraph-mcp not on PATH -- pip install tigergraph-mcp")
        return False

    ok = True
    tools = [t["name"] for t in seen.get(2, {}).get("result", {}).get("tools", [])]
    if not tools:
        print("  no tool list came back")
        return False
    print(f"  server exposes {len(tools)} tools")
    for want in ("run_installed_query", "run_query", "install_query", "show_query"):
        hit = [t for t in tools if t.endswith(want)]
        print(f"    [{'ok  ' if hit else 'FAIL'}] {hit[0] if hit else want}")
        ok = ok and bool(hit)

    call = seen.get(3, {})
    text = "".join(c.get("text", "") for c in call.get("result", {}).get("content", []))
    if '"success": true' not in text:
        print(f"    [FAIL] region_novelty through MCP: {(call.get('error') or text)!s:.200}")
        return False
    good = "42" in text
    print(f"    [{'ok  ' if good else 'FAIL'}] region_novelty via MCP returns the "
          f"hand-verified 42 prior transactions in region 330.0")
    return ok and good


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="mint a new token only")
    ap.add_argument("--check", action="store_true", help="verify the server only")
    args = ap.parse_args()

    if args.check:
        sys.exit(0 if check() else 1)

    print(f"wiring tigergraph-mcp to {cfg.TG_HOST} graph={cfg.TG_GRAPH}")
    token, expiry = mint_token()
    print(f"  token minted (expires {expiry})")
    write_env(token)
    if not args.refresh:
        write_mcp_json()
        print("\nverifying")
        ok = check()
        print("\n" + ("MCP server is wired to the graph" if ok else "MCP check failed"))
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
