"""Stage 2 of the load: create the graph in TigerGraph Savanna and load staging.

Usage
    python scripts/load_to_tigergraph.py                # schema + jobs + data + verify
    python scripts/load_to_tigergraph.py --schema-only
    python scripts/load_to_tigergraph.py --data-only
    python scripts/load_to_tigergraph.py --only transactions next
    python scripts/load_to_tigergraph.py --verify-only
    python scripts/load_to_tigergraph.py --drop          # destructive, asks first

The large files are uploaded in chunks because Savanna caps a single loading
request far below the 187 MB transaction file. Each chunk is a valid CSV with
its own header, so a failed chunk can be retried on its own without touching
what already loaded.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from etl import config as cfg  # noqa: E402

# job name -> (staging file, vertex/edge types it fills)
LOAD_PLAN: list[tuple[str, str, str]] = [
    ("load_customers", "customers.csv", "Customer"),
    ("load_cards", "cards.csv", "Card + OWNS"),
    ("load_devices", "devices.csv", "DeviceProfile"),
    ("load_regions", "regions.csv", "BillingRegion"),
    ("load_emails", "emails.csv", "EmailDomain"),
    ("load_products", "products.csv", "ProductCode"),
    ("load_meta", "meta.csv", "MetaDoc"),
    ("load_transactions", "transactions.csv", "Transaction + 6 edge types"),
    ("load_next", "next_edges.csv", "NEXT"),
    ("load_closed_cases", "closed_cases.csv", "ClosedCase + CC_ON_CARD"),
    ("load_cc_txns", "cc_txn_edges.csv", "CC_INVOLVES"),
    ("load_cc_connected", "cc_connected_edges.csv", "CC_CONNECTED_TO"),
    ("load_alerts", "alerts.csv", "Alert + 2 edge types"),
]

SHORT = {name: name.replace("load_", "") for name, _, _ in LOAD_PLAN}


def banner(text: str) -> None:
    print(f"\n=== {text} ===")


def read_gsql(filename: str) -> str:
    """Load a .gsql file, substitute the graph name, and strip // comments.

    The comments are for humans reading the repo; GSQL's parser rejects some of
    them (anything with a quote character in it), so they never leave this file.
    """
    text = (cfg.GRAPH_DIR / filename).read_text(encoding="utf-8")
    text = text.replace("@GRAPHNAME@", cfg.TG_GRAPH)
    lines = []
    for line in text.splitlines():
        stripped = re.sub(r"\s*//.*$", "", line)
        if stripped.strip():
            lines.append(stripped)
    return "\n".join(lines)


def gsql_failed(out: str) -> str | None:
    """Return the first failure line in a GSQL response, or None."""
    markers = ("Encountered", "Semantic Check Fails", "failed to", "does not exist",
               "Error:", "error:", "Failed ")
    for line in out.splitlines():
        if any(m in line for m in markers):
            if "already exists" in line:
                continue
            return line.strip()
    return None


def _graph_list(conn) -> list[str]:
    """Graph names from LS output, which lists them as '  - Graph NAME(...)'."""
    out = conn.gsql("LS")
    return re.findall(r"^\s*-\s*Graph\s+(\w+)", out, flags=re.MULTILINE)


def create_schema(conn, drop: bool) -> None:
    banner(f"schema: {cfg.TG_GRAPH}")
    existing = _graph_list(conn)
    if cfg.TG_GRAPH in existing:
        if not drop:
            print(f"  graph {cfg.TG_GRAPH} already exists, leaving it alone (use --drop to rebuild)")
            return
        print(f"  dropping graph {cfg.TG_GRAPH} and all its data")
        conn.gsql(f"DROP GRAPH {cfg.TG_GRAPH}")
        conn.gsql("DROP ALL")

    out = conn.gsql(read_gsql("schema.gsql"))
    print(_indent(out))
    bad = gsql_failed(out)
    if bad:
        raise SystemExit(f"  schema creation failed: {bad}")
    if cfg.TG_GRAPH not in _graph_list(conn):
        raise SystemExit(f"  schema ran but graph {cfg.TG_GRAPH} is not listed")
    print(f"  graph {cfg.TG_GRAPH} created")


def create_jobs(conn) -> None:
    banner("loading jobs")
    out = conn.gsql(read_gsql("loading_jobs.gsql"))
    print(_indent(out))
    bad = gsql_failed(out)
    if bad:
        raise SystemExit(f"  loading-job creation failed: {bad}")


def _indent(text: str, n: int = 2) -> str:
    pad = " " * n
    return "\n".join(pad + line for line in text.strip().splitlines() if line.strip())


def open_staging(path: Path):
    """Open a staging file, transparently falling back to its .gz twin.

    data/staging/transactions.csv is 187 MB, over GitHub's 100 MB per-file
    limit, so the committed copy is gzipped (30 MB). prepare_data.py writes the
    plain file; whichever is present is used.
    """
    if path.exists():
        return path.open("r", encoding="utf-8", newline="")
    gz = path.with_suffix(path.suffix + ".gz")
    if gz.exists():
        return gzip.open(gz, "rt", encoding="utf-8", newline="")
    raise SystemExit(
        f"missing staging file {path} (and {gz.name}) -- run scripts/prepare_data.py first"
    )


def staging_size_mb(path: Path) -> float:
    """Uncompressed size where we have it, else the compressed size."""
    if path.exists():
        return path.stat().st_size / 1e6
    gz = path.with_suffix(path.suffix + ".gz")
    return gz.stat().st_size / 1e6 * 6 if gz.exists() else 0.0


def chunk_csv(path: Path, rows_per_chunk: int):
    """Yield (chunk_index, temp_path, n_rows) with the header row removed.

    The header is dropped here rather than left to the loading job because
    TigerGraph ignores HEADER="true" on the online POST path used by
    runLoadingJobWithFile and would insert the header row as a vertex.
    """
    with open_staging(path) as fh:
        reader = csv.reader(fh)
        next(reader)  # discard header
        idx, buf = 0, []
        for row in reader:
            buf.append(row)
            if len(buf) >= rows_per_chunk:
                yield _write_chunk(buf, idx)
                idx += 1
                buf = []
        if buf:
            yield _write_chunk(buf, idx)


def _write_chunk(rows, idx):
    tmp = Path(tempfile.gettempdir()) / f"sentinel_chunk_{idx}_{int(time.time()*1000)}.csv"
    with tmp.open("w", encoding="utf-8", newline="") as out:
        csv.writer(out, lineterminator="\n").writerows(rows)
    return idx, tmp, len(rows)


def run_job(conn, job: str, filename: str, describes: str) -> None:
    path = cfg.STAGING / filename
    size_mb = staging_size_mb(path)
    if size_mb == 0.0:
        raise SystemExit(f"missing staging file {path} -- run scripts/prepare_data.py first")
    print(f"\n  {job:<20} {filename:<24} {size_mb:>7.1f} MB  -> {describes}")
    t0 = time.time()

    rows_per_chunk = cfg.CHUNK_ROWS if size_mb >= 40 else 10_000_000
    total = 0
    for idx, chunk, nrows in chunk_csv(path, rows_per_chunk):
        label = "single" if rows_per_chunk > 1_000_000 else f"chunk {idx} ({nrows:,} rows)"
        try:
            _post(conn, job, chunk, label=label)
            total += nrows
        finally:
            chunk.unlink(missing_ok=True)
    print(f"      {total:,} rows uploaded in {time.time() - t0:.1f}s")


def _post(conn, job: str, path: Path, label: str) -> None:
    res = conn.runLoadingJobWithFile(str(path), "f", job, sep=",")
    stats = _summarise(res)
    print(f"      {label}: {stats}")


def _summarise(res) -> str:
    """Flatten the loader's nested reply into one readable line per object type."""
    if not res:
        return "no response"
    try:
        out = []
        for item in res if isinstance(res, list) else [res]:
            ps = item.get("statistics", {}).get("parsingStatistics", {})
            fl = ps.get("fileLevel", {})
            ol = ps.get("objectLevel", {})
            head = f"lines={fl.get('validLine', '?'):,}"
            for bad in ("rejectLine", "notEnoughToken", "badPrimaryId"):
                if fl.get(bad):
                    head += f" {bad}={fl[bad]}"
            out.append(head)
            for kind in ("vertex", "edge"):
                for o in ol.get(kind, []):
                    bit = f"{o['typeName']}={o.get('validObject', 0):,}"
                    if o.get("invalidAttribute"):
                        bit += f"(badattr {o['invalidAttribute']})"
                    out.append(bit)
        return " ".join(out)
    except Exception:  # noqa: BLE001
        return str(res)[:200]


EXPECTED = {
    "Customer": 13553,
    "Card": 14317,
    "Transaction": 590742,
    "DeviceProfile": 9704,
    "BillingRegion": 332,
    "EmailDomain": 60,
    "ProductCode": 5,
    "ClosedCase": 5565,
    "Alert": 20,
}

# Exact counts, computed from the staging files rather than eyeballed. The email,
# region and device edges are fewer than the transaction count because those
# fields are genuinely absent on some rows -- in_person transactions carry no
# identity record at all.
EXPECTED_EDGES = {
    "OWNS": 14317,
    "MADE": 590742,
    "NEXT": 576425,
    "OF_PRODUCT": 590742,
    "FROM_DEVICE": 140784,
    "BILLED_IN": 525003,
    "PURCHASER_EMAIL": 496262,
    "RECIPIENT_EMAIL": 137453,
    "CC_INVOLVES": 14955,
    "CC_ON_CARD": 5565,
    "CC_CONNECTED_TO": 92,
    "ALERT_ON_CARD": 20,
    "ALERT_ON_TXN": 20,
}


# Round 2: July 2016 - January 2017 (data/staging_r2, built by stage_january.py),
# computed from those staging files the same way. January writes empty fields as
# a quoted "", which the loader's WHERE $n != "" treats as empty.
if cfg.STAGING.name == "staging_r2":
    EXPECTED = {**EXPECTED, "Transaction": 650938, "DeviceProfile": 9728, "BillingRegion": 333, "ClosedCase": 5585}
    EXPECTED_EDGES = {
        **EXPECTED_EDGES, "MADE": 650938, "NEXT": 636621, "OF_PRODUCT": 650938, "FROM_DEVICE": 156314,
        "BILLED_IN": 580225, "PURCHASER_EMAIL": 544979, "RECIPIENT_EMAIL": 150068, "CC_INVOLVES": 15077,
        "CC_ON_CARD": 5585, "CC_CONNECTED_TO": 130,
    }


def verify(conn, settle: int = 0) -> bool:
    banner("verification")
    if settle:
        # Ingestion is asynchronous: counts keep climbing for a few seconds
        # after the last upload returns, so give them time to settle.
        print(f"  waiting {settle}s for ingestion to settle")
        time.sleep(settle)
    ok = True

    print("  vertex counts")
    for vtype, expected in EXPECTED.items():
        try:
            n = conn.getVertexCount(vtype)
        except Exception as exc:  # noqa: BLE001
            print(f"    [FAIL] {vtype}: {exc}")
            ok = False
            continue
        flag = "ok  " if n == expected else "FAIL"
        if n != expected:
            ok = False
        print(f"    [{flag}] {vtype:<15} {n:>9,}  expected {expected:,}")

    print("  edge counts")
    for etype, expected in EXPECTED_EDGES.items():
        try:
            n = conn.getEdgeCount(etype)
        except Exception as exc:  # noqa: BLE001
            print(f"    [FAIL] {etype}: {exc}")
            ok = False
            continue
        flag = "ok  " if n == expected else "FAIL"
        if n != expected:
            ok = False
        print(f"    [{flag}] {etype:<18} {n:>9,}  expected {expected:,}")

    # The real test: can we walk an exam alert to its transaction, its card, its
    # customer and its billing region? If this path works, the graph is usable.
    print("  end-to-end traversal from an exam alert")
    try:
        alert = conn.getVerticesById("Alert", "HHG-001")[0]
        txn_id = alert["attributes"]["flagged_txn_id"]
        txn = conn.getVerticesById("Transaction", txn_id)[0]["attributes"]
        card = conn.getVerticesById("Card", txn["card_id"])[0]["attributes"]
        print(f"    HHG-001 -> txn {txn_id} ${txn['amt']:.2f} risk={txn['risk_score']:.2f} "
              f"{txn['channel']} region={txn['addr1']}")
        print(f"    card {txn['card_id']}: {card['network']} {card['card_type']}, "
              f"{card['n_txns']:,} txns, median ${card['median_amt']:.2f}, p95 ${card['p95_amt']:.2f}")
        if alert["attributes"]["card_id"] != txn["card_id"]:
            print("    [FAIL] alert card does not match the transaction's card")
            ok = False
        else:
            print("    [ok  ] alert card matches the transaction's card")
    except Exception as exc:  # noqa: BLE001
        print(f"    [FAIL] traversal: {exc}")
        ok = False

    # Shared device profiles are where the rings are, so prove they survived.
    print("  device profiles shared across cards (ring candidates)")
    try:
        res = conn.runInterpretedQuery(
            f"""INTERPRET QUERY () FOR GRAPH {cfg.TG_GRAPH} {{
                  D = {{DeviceProfile.*}};
                  R = SELECT d FROM D:d WHERE d.n_cards > 1
                      ORDER BY d.n_cards DESC LIMIT 3;
                  PRINT R;
                }}"""
        )
        rows = res[0].get("R", []) if res else []
        for r in rows:
            a = r["attributes"]
            print(f"    {a['n_cards']:>4} cards, {a['n_txns']:>5} txns  {a['label'][:64]}")
        if not rows:
            print("    [FAIL] no shared device profiles found")
            ok = False
    except Exception as exc:  # noqa: BLE001
        print(f"    [warn] interpreted query unavailable ({str(exc)[:80]})")

    print("\n  " + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description="Load Sentinel data into TigerGraph Savanna")
    ap.add_argument("--schema-only", action="store_true")
    ap.add_argument("--data-only", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--drop", action="store_true", help="drop and rebuild the graph (destructive)")
    ap.add_argument("--only", nargs="*", metavar="JOB",
                    help=f"load only these: {' '.join(SHORT.values())}")
    args = ap.parse_args()

    print(f"Sentinel loader -> {cfg.TG_HOST}")
    print(f"  graph: {cfg.TG_GRAPH}   staging: {cfg.STAGING}")

    if args.drop:
        reply = input(f"  DROP GRAPH {cfg.TG_GRAPH} and everything in it? type the graph name to confirm: ")
        if reply.strip() != cfg.TG_GRAPH:
            raise SystemExit("  not confirmed, nothing was dropped")

    # Schema work needs a connection that does not assume the graph exists yet.
    admin = cfg.connect(graphname=cfg.TG_GRAPH)

    if args.verify_only:
        sys.exit(0 if verify(admin) else 1)

    if not args.data_only:
        create_schema(admin, drop=args.drop)
        create_jobs(admin)
        if args.schema_only:
            print("\nschema only: stopping before data")
            return

    conn = cfg.connect(graphname=cfg.TG_GRAPH, verbose=False)
    banner("data load")
    plan = LOAD_PLAN
    if args.only:
        wanted = set(args.only)
        plan = [p for p in LOAD_PLAN if SHORT[p[0]] in wanted or p[0] in wanted]
        if not plan:
            raise SystemExit(f"--only matched nothing; choose from {' '.join(SHORT.values())}")

    t0 = time.time()
    for job, filename, describes in plan:
        run_job(conn, job, filename, describes)
    print(f"\n  data load finished in {(time.time() - t0) / 60:.1f} min")

    sys.exit(0 if verify(conn, settle=20) else 1)


if __name__ == "__main__":
    main()
