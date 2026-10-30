"""Engine and session lifecycle for the operational store."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from api.store.models import Base
from sentinel.config.settings import Settings

logger = logging.getLogger(__name__)


class Database:
    """One engine, one session factory, created at startup and closed at shutdown.

    Responsibility: connection lifecycle and schema creation. It holds no
    queries — those live in the repositories.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._engine: AsyncEngine = create_async_engine(url, future=True)
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> Database:
        # backend/var/ is gitignored and absent on a fresh clone; the engine
        # will not create the directory for us and the failure is obscure.
        if settings.db_url.startswith("sqlite"):
            path = settings.db_url.split("///", 1)[-1]
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        return cls(settings.db_url)

    @property
    def url(self) -> str:
        return self._url

    async def create_all(self) -> None:
        """Create any missing table. No migration tool: the schema is new."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("operational store ready at %s", self._url)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """One transaction. Commits on clean exit, rolls back on any exception."""
        async with self._sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def aclose(self) -> None:
        await self._engine.dispose()
