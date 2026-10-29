"""Central configuration for Sentinel. Everything reads the .env at the repo root."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

# --- TigerGraph Savanna -------------------------------------------------------
TG_HOST = os.getenv("TG_HOST", "").rstrip("/")
TG_SECRET = os.getenv("TG_SECRET") or os.getenv("TG_KEY", "")  # Savanna's console calls it the key
TG_GRAPH = os.getenv("TG_GRAPH", "GRAPH_GOA")
TG_RESTPP_PORT = os.getenv("TG_RESTPP_PORT", "443")
TG_GSQL_PORT = os.getenv("TG_GSQL_PORT", "443")

# --- Paths --------------------------------------------------------------------
DATA = ROOT
# Round 2 loads data/staging_r2 (Jul 2016 - Jan 2017) by setting STAGING_DIR.
STAGING = Path(os.getenv("STAGING_DIR", str(ROOT / "data" / "staging")))
GRAPH_DIR = ROOT / "graph"
CASES_DIR = ROOT / "cases"

RAW_TRANSACTIONS = DATA / "transactions.csv"
RAW_IDENTITY = DATA / "identity.csv"
RAW_CLOSED_CASES = DATA / "closed_cases_history.csv"
RAW_CASE_PACK = DATA / "case_pack.csv"

# --- Column projection (PRD 6.3) ----------------------------------------------
# All 590,742 rows are loaded. Of the 393 columns we keep every analytically
# useful one and pack the repetitive families into ordered, pipe-joined strings
# so the row stays small enough to upload to Savanna.

C_COLS = [f"C{i}" for i in range(1, 15)]          # 14 count features
D_COLS = [f"D{i}" for i in range(1, 16)]          # 15 day-delta features
M_COLS = [f"M{i}" for i in range(1, 10)]          # 9 match flags

# Group heads from Vesta's engineered blocks. Kept as named JSON so evidence can
# cite them honestly as unnamed model features rather than pretending to know
# what V127 means.
V_COLS = [
    "V12", "V19", "V36", "V44", "V54", "V62", "V78", "V86",
    "V95", "V99", "V127", "V130", "V143", "V165", "V189", "V201",
    "V207", "V210", "V258", "V264", "V267", "V283", "V294", "V317",
]

# Ordering contract for the packed strings, written into the graph as a MetaDoc
# vertex so nothing downstream has to guess.
PACKED_ORDER = {"c_feats": C_COLS, "d_feats": D_COLS, "m_feats": M_COLS, "v_feats": V_COLS}

# Sentinel used for missing numerics. TigerGraph's loader turns an empty token
# into 0, which would be indistinguishable from a real zero, so missing values
# are written as -1 and must be read as "unknown".
MISSING_NUM = -1

# Upload chunk size (rows) for runLoadingJobWithFile; Savanna caps a single
# request well below the size of the full transaction file.
CHUNK_ROWS = int(os.getenv("TG_CHUNK_ROWS", "120000"))


def connect(graphname: str | None = None, verbose: bool = True):
    """Return an authenticated pyTigerGraph connection to Savanna."""
    import pyTigerGraph as tg

    if not TG_HOST or not TG_SECRET:
        raise RuntimeError("TG_HOST and TG_SECRET must be set in .env")

    conn = tg.TigerGraphConnection(
        host=TG_HOST,
        graphname=graphname or TG_GRAPH,
        gsqlSecret=TG_SECRET,
        restppPort=TG_RESTPP_PORT,
        gsPort=TG_GSQL_PORT,
    )
    conn.getToken(TG_SECRET)
    if verbose:
        print(f"  connected to {TG_HOST} (TigerGraph {conn.getVer()}) graph={conn.graphname}")
    return conn
