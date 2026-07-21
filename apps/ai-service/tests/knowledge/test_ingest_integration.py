from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_pgvector_integration import (
    REPOSITORY_ROOT,
    TENANT_A,
    TENANT_B,
    _apply_phase_one,
    _docker_available,
    _drop_vector_extension,
    _role_url,
    _run_alembic,
    _run_bootstrap,
)
from testcontainers.postgres import PostgresContainer

from app.config import Settings
from app.db import Database
from app.knowledge.embedding.deterministic import DeterministicEmbeddingProvider
from app.knowledge.ingest import EmbeddingWorker, KnowledgeIngestor
from app.knowledge.repository import PostgresKnowledgeRepository

pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker Engine unavailable; live ingestion integration disabled",
)


@pytest.fixture(scope="module")
def ingestion_runtime_url() -> Iterator[str]:
    container = PostgresContainer(
        "pgvector/pgvector:pg16",
        username="postgres",
        password="postgres-test",
        dbname="workorders",
        driver="asyncpg",
    )
    container.with_env("FLYWAY_PASSWORD", "flyway-owner-test")
    container.with_env("WORK_ORDER_DB_PASSWORD", "work-order-test")
    container.with_env("AI_DB_PASSWORD", "ai-app-test")
    container.with_env("ANALYTICS_DB_PASSWORD", "analytics-test")
    container.with_volume_mapping(
        str(REPOSITORY_ROOT / "infra" / "postgres" / "init"),
        "/docker-entrypoint-initdb.d",
        "ro",
    )
    with container:
        admin_url = container.get_connection_url(driver="asyncpg")
        migration_url = _role_url(admin_url, "flyway_owner", "flyway-owner-test")
        runtime_url = _role_url(admin_url, "ai_app", "ai-app-test")
        asyncio.run(_drop_vector_extension(admin_url))
        bootstrap = _run_bootstrap(admin_url)
        assert bootstrap.returncode == 0, bootstrap.stdout + bootstrap.stderr
        asyncio.run(_apply_phase_one(migration_url))
        migration = _run_alembic(migration_url, "upgrade", "head")
        assert migration.returncode == 0, migration.stdout + migration.stderr
        yield runtime_url


@pytest.mark.asyncio
async def test_live_ingestion_is_idempotent_and_only_latest_complete_version_activates(
    ingestion_runtime_url: str,
) -> None:
    # Verify the runtime URL is independently usable before wrapping it in Database.
    probe = create_async_engine(ingestion_runtime_url)
    async with probe.connect() as connection:
        assert await connection.scalar(text("SELECT 1")) == 1
    await probe.dispose()

    database = Database(Settings(ai_database_url=ingestion_runtime_url, _env_file=None))
    repository = PostgresKnowledgeRepository(database)
    provider = DeterministicEmbeddingProvider()
    ingestor = KnowledgeIngestor(repository, model_key=provider.model_key)
    worker = EmbeddingWorker(repository, provider)
    try:
        first = await ingestor.ingest(
            TENANT_A,
            "task4-live-policy",
            "Task 4 Live Policy",
            "## Rule\nThe original rule applies.",
            "test://task4/live",
        )
        duplicate = await ingestor.ingest(
            TENANT_A,
            "task4-live-policy",
            "Task 4 Live Policy",
            "## Rule\nThe original rule applies.",
            "test://task4/live",
        )
        changed = await ingestor.ingest(
            TENANT_A,
            "task4-live-policy",
            "Task 4 Live Policy",
            "## Rule\nThe changed rule applies.",
            "test://task4/live",
        )
        assert (first.version, first.skipped) == (1, False)
        assert (duplicate.version, duplicate.skipped) == (1, True)
        assert (changed.version, changed.skipped) == (2, False)

        completed = await worker.run_once(TENANT_A, limit=100)
        assert completed.claimed == 2
        assert completed.succeeded == 2
        assert completed.retried == 0
        assert completed.failed == 0

        async with database.session(TENANT_A) as session:
            documents = (
                await session.execute(
                    text(
                        """
                        SELECT version, status
                        FROM knowledge_document
                        WHERE tenant_id = :tenant_id
                          AND document_key = 'task4-live-policy'
                        ORDER BY version
                        """
                    ),
                    {"tenant_id": str(TENANT_A)},
                )
            ).all()
            assert documents == [(1, "INACTIVE"), (2, "ACTIVE")]
            assert (
                await session.scalar(
                    text(
                        """
                    SELECT count(*) FROM embedding_job
                    WHERE tenant_id = :tenant_id AND status = 'SUCCEEDED'
                    """
                    ),
                    {"tenant_id": str(TENANT_A)},
                )
                == 2
            )
            assert (
                await session.scalar(
                    text(
                        """
                    SELECT count(*) FROM knowledge_embedding
                    WHERE tenant_id = :tenant_id AND model_key = :model_key
                    """
                    ),
                    {"tenant_id": str(TENANT_A), "model_key": provider.model_key},
                )
                == 2
            )

        async with database.session(TENANT_B) as session:
            assert await session.scalar(text("SELECT count(*) FROM knowledge_document")) == 0
    finally:
        await database.dispose()
