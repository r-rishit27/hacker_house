"""Typed, validated, fail-fast configuration.

Replaces ``sentinel/config.py``'s module-level globals and its import-time
``load_dotenv()``. Nothing outside this module reads ``os.environ``.

Two deliberate absences: ``openai_model`` and ``openai_embedding_model`` have no
defaults. A hard-coded model id is how an hour goes to a 404 on demo day, so the
process refuses to start without them rather than guessing.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: Repository root — backend/sentinel/config/settings.py -> up three.
ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Every knob the system has, in one validated object.

    Responsibility: read and validate the environment once. It holds no
    connections and performs no I/O beyond reading ``.env``.
    Collaborators: injected into every class that needs configuration; nothing
    constructs a ``Settings`` of its own except :func:`get_settings`.
    """

    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── TigerGraph ───────────────────────────────────────────────────────────
    tg_host: str
    #: Savanna's console labels the secret "key"; accept either name.
    tg_secret: SecretStr = Field(validation_alias=AliasChoices("tg_secret", "tg_key"))
    tg_graph: str = "GRAPH_GOA"
    #: Savanna terminates both REST++ and GSQL on 443.
    tg_restpp_port: int = 443
    tg_gsql_port: int = 443
    #: Bearer token written by scripts/setup_mcp.py. Optional: the secret can mint one.
    tg_api_token: SecretStr | None = None
    tg_query_timeout_s: float = 120.0
    #: Cap on concurrent graph calls so a benchmark batch cannot flood the workspace.
    tg_max_concurrency: int = 8
    #: Retries for the ~45 s cold start after the workspace auto-stops.
    tg_connect_retries: int = 4

    # ── OpenAI ───────────────────────────────────────────────────────────────
    openai_api_key: SecretStr
    openai_model: str
    openai_embedding_model: str
    #: 1536 -> 256 cuts the ClosedCase write-back from ~130 MB to ~25 MB.
    openai_embedding_dimensions: int = 256
    openai_timeout_s: float = 90.0
    openai_max_retries: int = 3

    # ── Paths ────────────────────────────────────────────────────────────────
    root: Path = ROOT
    cases_dir: Path = ROOT / "cases"
    runs_dir: Path = ROOT / "runs"
    exploration_dir: Path = ROOT / "exploration"
    staging_dir: Path = ROOT / "data" / "staging"
    case_pack_path: Path = ROOT / "case_pack.csv"
    elt_path: Path = Path(__file__).resolve().parents[1] / "evidence" / "elt.json"
    corpus_dir: Path = ROOT / "backend" / "var" / "corpus"
    db_url: str = f"sqlite+aiosqlite:///{ROOT / 'backend' / 'var' / 'sentinel.db'}"

    # ── Budgets: BUILD_PLAN's loop guards, promoted to settings ──────────────
    max_tool_calls_per_run: int = 25
    #: The answer format has exactly `initial` and `final`. One round, enforced.
    max_evidence_rounds: int = 1
    max_tokens_per_run: int = 120_000
    max_usd_per_run: float = 1.50
    max_run_seconds: int = 300
    max_concurrent_investigations: int = 4

    # ── API ──────────────────────────────────────────────────────────────────
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    # NoDecode: without it pydantic-settings JSON-decodes a list field read from
    # .env *before* any validator runs, so the comma-separated form below — the
    # only form a .env file can hold — raises SettingsError at import time.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            # Next falls back to 3001 when 3000 is taken, and a console that
            # silently cannot reach the API is the worst way to discover it.
            "http://localhost:3001",
            "http://127.0.0.1:3001",
        ]
    )
    default_role: str = "analyst"
    sse_keepalive_seconds: int = 15
    sse_subscriber_queue_size: int = 256

    @field_validator("tg_host")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string, because that is what .env files hold."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def restpp_base(self) -> str:
        """The REST++ base URL. Savanna serves it on the workspace host itself."""
        return f"{self.tg_host}/restpp"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The one Settings instance. Cached so ``.env`` is read once per process."""
    return Settings()  # type: ignore[call-arg]  # values come from the environment
