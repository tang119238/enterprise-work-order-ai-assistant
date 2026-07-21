from __future__ import annotations

import asyncio
import sys

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

EXTENSION_SQL = "CREATE EXTENSION IF NOT EXISTS vector"
FAILURE_MESSAGE = (
    "pgvector bootstrap failed. Set PGVECTOR_ADMIN_DATABASE_URL to an administrator URL "
    "that may run CREATE EXTENSION IF NOT EXISTS vector, then retry."
)


class PgvectorBootstrapSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    pgvector_admin_database_url: str = Field(
        validation_alias="PGVECTOR_ADMIN_DATABASE_URL",
    )


async def bootstrap_vector_extension(database_url: str) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(EXTENSION_SQL))
    finally:
        await engine.dispose()


def main() -> int:
    try:
        settings = PgvectorBootstrapSettings.model_validate({})
        asyncio.run(bootstrap_vector_extension(settings.pgvector_admin_database_url))
    except Exception:
        print(FAILURE_MESSAGE, file=sys.stderr)
        return 1

    print("pgvector extension is ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
