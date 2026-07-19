from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


class Database:
    def __init__(self, settings: Settings | None = None) -> None:
        runtime_settings = settings or Settings()
        self._engine: AsyncEngine = create_async_engine(
            runtime_settings.ai_database_url,
            pool_pre_ping=True,
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
        )
        self._closed = False

    @asynccontextmanager
    async def session(self, tenant_id: UUID) -> AsyncIterator[AsyncSession]:
        if not isinstance(tenant_id, UUID):
            raise TypeError("tenant_id must be a non-null UUID")
        if self._closed:
            raise RuntimeError("database has been disposed")

        async with self._session_factory.begin() as session:
            await session.execute(
                text("select set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            yield session

    async def dispose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._engine.dispose()
