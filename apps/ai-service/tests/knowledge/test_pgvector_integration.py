from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg
import pytest
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.core.docker_client import DockerClient
from testcontainers.postgres import PostgresContainer

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
AI_SERVICE_ROOT = REPOSITORY_ROOT / "apps" / "ai-service"
MIGRATIONS = (
    REPOSITORY_ROOT
    / "apps"
    / "work-order-service"
    / "src"
    / "main"
    / "resources"
    / "db"
    / "migration"
)
TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = UUID("22222222-2222-2222-2222-222222222222")


def _docker_available() -> bool:
    try:
        return bool(DockerClient().client.ping())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker Engine unavailable; pgvector Testcontainers integration disabled",
)


def _role_url(admin_url: str, username: str, password: str) -> str:
    return make_url(admin_url).set(username=username, password=password).render_as_string(
        hide_password=False
    )


async def _apply_phase_one(migration_url: str) -> None:
    connection = await asyncpg.connect(migration_url.replace("+asyncpg", ""))
    try:
        migrations = sorted(
            MIGRATIONS.glob("V*.sql"),
            key=lambda path: int(path.name[1:].split("__")[0]),
        )
        for migration in migrations:
            await connection.execute(migration.read_text(encoding="utf-8"))
    finally:
        await connection.close()


def _run_alembic(migration_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["AI_MIGRATION_DATABASE_URL"] = migration_url
    return subprocess.run(
        [
            str(REPOSITORY_ROOT / ".venv" / "Scripts" / "python.exe"),
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            *arguments,
        ],
        cwd=AI_SERVICE_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def database_urls() -> Iterator[dict[str, str]]:
    init_directory = REPOSITORY_ROOT / "infra" / "postgres" / "init"
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
    container.with_volume_mapping(str(init_directory), "/docker-entrypoint-initdb.d", "ro")

    with container:
        admin_url = container.get_connection_url(driver="asyncpg")
        migration_url = _role_url(admin_url, "flyway_owner", "flyway-owner-test")
        runtime_url = _role_url(admin_url, "ai_app", "ai-app-test")
        asyncio.run(_apply_phase_one(migration_url))
        first = _run_alembic(migration_url, "upgrade", "head")
        assert first.returncode == 0, first.stdout + first.stderr
        second = _run_alembic(migration_url, "upgrade", "head")
        assert second.returncode == 0, second.stdout + second.stderr
        yield {
            "admin": admin_url,
            "migration": migration_url,
            "runtime": runtime_url,
        }


def _vector(values: int) -> str:
    return "[" + ",".join("0" for _ in range(values)) + "]"


async def _scalar(url: str, statement: str, *args: object) -> Any:
    connection = await asyncpg.connect(url.replace("+asyncpg", ""))
    try:
        return await connection.fetchval(statement, *args)
    finally:
        await connection.close()


async def _visible_chunk_count(url: str, tenant_setting: str | None = None) -> int:
    connection = await asyncpg.connect(url.replace("+asyncpg", ""))
    try:
        if tenant_setting is not None:
            await connection.execute(
                "select set_config('app.tenant_id', $1, false)",
                tenant_setting,
            )
        return int(await connection.fetchval("select count(*) from knowledge_chunk"))
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_live_schema_roles_rls_vectors_and_safe_downgrade(
    database_urls: dict[str, str],
) -> None:
    from app.config import Settings
    from app.db import Database

    admin_url = database_urls["admin"]
    migration_url = database_urls["migration"]
    runtime_url = database_urls["runtime"]
    database = Database(Settings(ai_database_url=runtime_url, _env_file=None))

    document_a = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1")
    document_b = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1")
    chunk_a = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2")
    chunk_b = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2")
    job_a = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3")
    job_b = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb3")

    try:
        for tenant_id, document_id, chunk_id, job_id in (
            (TENANT_A, document_a, chunk_a, job_a),
            (TENANT_B, document_b, chunk_b, job_b),
        ):
            async with database.session(tenant_id) as session:
                await session.execute(
                    text(
                        """
                        insert into knowledge_document
                            (id, tenant_id, document_key, title, source_type, source_uri,
                             content_hash, version, status)
                        values
                            (:id, :tenant_id, 'same-key', 'Synthetic policy', 'MARKDOWN',
                             'synthetic://policy', :content_hash, 1, 'ACTIVE')
                        """
                    ),
                    {
                        "id": document_id,
                        "tenant_id": tenant_id,
                        "content_hash": "a" * 64,
                    },
                )
                await session.execute(
                    text(
                        """
                        insert into knowledge_chunk
                            (id, tenant_id, document_id, chunk_key, section, content,
                             content_hash, token_count, ordinal, status)
                        values
                            (:id, :tenant_id, :document_id, 'same-chunk', 'Synthetic',
                             'Synthetic policy text only.', :content_hash, 4, 0, 'ACTIVE')
                        """
                    ),
                    {
                        "id": chunk_id,
                        "tenant_id": tenant_id,
                        "document_id": document_id,
                        "content_hash": "b" * 64,
                    },
                )
                await session.execute(
                    text(
                        """
                        insert into embedding_job
                            (id, tenant_id, document_id, chunk_id, business_key,
                             model_key, status)
                        values
                            (:id, :tenant_id, :document_id, :chunk_id,
                             'same-business-key', 'synthetic/512', 'PENDING')
                        """
                    ),
                    {
                        "id": job_id,
                        "tenant_id": tenant_id,
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                    },
                )

        async with database.session(TENANT_A) as session:
            await session.execute(
                text(
                    """
                    insert into knowledge_embedding
                        (tenant_id, chunk_id, model_key, dimensions, embedding, content_hash)
                    values
                        (:tenant_id, :chunk_id, 'synthetic/512', 512,
                         cast(:embedding as vector(512)), :content_hash)
                    """
                ),
                {
                    "tenant_id": TENANT_A,
                    "chunk_id": chunk_a,
                    "embedding": _vector(512),
                    "content_hash": "b" * 64,
                },
            )
        with pytest.raises(DBAPIError):
            async with database.session(TENANT_A) as session:
                await session.execute(
                    text(
                        """
                        insert into knowledge_embedding
                            (tenant_id, chunk_id, model_key, dimensions, embedding, content_hash)
                        values
                            (:tenant_id, :chunk_id, 'synthetic/511', 512,
                             cast(:embedding as vector(512)), :content_hash)
                        """
                    ),
                    {
                        "tenant_id": TENANT_A,
                        "chunk_id": chunk_a,
                        "embedding": _vector(511),
                        "content_hash": "b" * 64,
                    },
                )

        # The failed statement aborts its transaction, so use fresh scoped transactions below.
        assert await _visible_chunk_count(runtime_url) == 0
        assert await _visible_chunk_count(runtime_url, "") == 0

        for tenant_id, expected_chunk in ((TENANT_A, chunk_a), (TENANT_B, chunk_b)):
            async with database.session(tenant_id) as session:
                visible = (
                    await session.execute(text("select id from knowledge_chunk order by id"))
                ).scalars().all()
                assert visible == [expected_chunk]

        async with database.session(TENANT_A) as session:
            assert (
                await session.execute(
                    text(
                        """
                        update embedding_job set retry_count = retry_count + 1
                        where id = :job_id returning retry_count
                        """
                    ),
                    {"job_id": job_a},
                )
            ).scalar_one() == 1
            assert (
                await session.execute(
                    text("delete from embedding_job where id = :job_id returning id"),
                    {"job_id": job_a},
                )
            ).scalar_one() == job_a

        migration_engine = create_async_engine(migration_url)
        try:
            async with migration_engine.begin() as connection:
                await connection.execute(
                    text("select set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(TENANT_A)},
                )
                assert await connection.scalar(text("select count(*) from knowledge_chunk")) == 1
        finally:
            await migration_engine.dispose()

        admin = await asyncpg.connect(admin_url.replace("+asyncpg", ""))
        try:
            assert await admin.fetchval(
                "select extversion from pg_extension where extname = 'vector'"
            )
            assert await admin.fetchval(
                """
                select pg_catalog.format_type(a.atttypid, a.atttypmod)
                from pg_attribute a
                join pg_class c on c.oid = a.attrelid
                where c.relname = 'knowledge_embedding' and a.attname = 'embedding'
                """
            ) == "vector(512)"
            assert await admin.fetchval(
                """
                select pg_catalog.format_type(a.atttypid, a.atttypmod)
                from pg_attribute a
                join pg_class c on c.oid = a.attrelid
                where c.relname = 'knowledge_embedding' and a.attname = 'dimensions'
                """
            ) == "smallint"
            constraints = "\n".join(
                row["definition"]
                for row in await admin.fetch(
                    """
                    select pg_get_constraintdef(oid) as definition
                    from pg_constraint
                    where conrelid in (
                        'knowledge_document'::regclass, 'knowledge_chunk'::regclass,
                        'knowledge_embedding'::regclass, 'embedding_job'::regclass
                    )
                    order by conname
                    """
                )
            )
            assert "CHECK ((dimensions = 512))" in constraints
            assert "FOREIGN KEY (tenant_id, document_id)" in constraints
            assert "FOREIGN KEY (tenant_id, chunk_id)" in constraints
            assert "UNIQUE (tenant_id, document_key, version)" in constraints
            assert "PRIMARY KEY (tenant_id, chunk_id, model_key)" in constraints

            index_rows = await admin.fetch(
                "select indexname, indexdef from pg_indexes where schemaname='public'"
            )
            indexes = {row["indexname"]: row["indexdef"] for row in index_rows}
            assert "(tenant_id, model_key)" in indexes["idx_knowledge_embedding_tenant_model"]
            hnsw = indexes["idx_knowledge_embedding_hnsw"]
            assert "USING hnsw" in hnsw
            assert "vector_cosine_ops" in hnsw
            assert "m='16'" in hnsw or "m = 16" in hnsw
            assert "ef_construction='64'" in hnsw or "ef_construction = 64" in hnsw

            rls_rows = await admin.fetch(
                """
                select c.relname, c.relrowsecurity, c.relforcerowsecurity,
                       p.qual, p.with_check
                from pg_class c
                join pg_policies p on p.tablename = c.relname and p.schemaname = 'public'
                where c.relname = any($1::text[])
                order by c.relname
                """,
                [
                    "knowledge_document",
                    "knowledge_chunk",
                    "knowledge_embedding",
                    "embedding_job",
                ],
            )
            assert len(rls_rows) == 4
            for row in rls_rows:
                assert row["relrowsecurity"] is True
                assert row["relforcerowsecurity"] is True
                safe_setting = "NULLIF(current_setting('app.tenant_id'::text, true), ''::text)"
                assert safe_setting in row["qual"]
                assert row["qual"] == row["with_check"]

            grants = await admin.fetch(
                """
                select table_name, privilege_type
                from information_schema.role_table_grants
                where grantee = 'ai_app'
                order by table_name, privilege_type
                """
            )
            grant_pairs = {(row["table_name"], row["privilege_type"]) for row in grants}
            knowledge_tables = {
                "knowledge_document",
                "knowledge_chunk",
                "knowledge_embedding",
                "embedding_job",
            }
            assert grant_pairs == {
                (table, privilege)
                for table in knowledge_tables
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
            }
            leaked_knowledge_grants = await admin.fetchval(
                """
                select count(*) from information_schema.role_table_grants
                where grantee in ('work_order_app', 'analytics_reader')
                  and table_name = any($1::text[])
                """,
                list(knowledge_tables),
            )
            assert leaked_knowledge_grants == 0
            roles = await admin.fetch(
                """
                select rolname, rolsuper, rolbypassrls
                from pg_roles where rolname in ('ai_app', 'flyway_owner')
                order by rolname
                """
            )
            assert [dict(role) for role in roles] == [
                {"rolname": "ai_app", "rolsuper": False, "rolbypassrls": False},
                {"rolname": "flyway_owner", "rolsuper": False, "rolbypassrls": False},
            ]
            extension_owner = await admin.fetchval(
                """
                select owner.rolname
                from pg_extension extension
                join pg_roles owner on owner.oid = extension.extowner
                where extension.extname = 'vector'
                """
            )
            assert extension_owner == "postgres"
            owners = await admin.fetch(
                """
                select distinct owner.rolname
                from pg_class c join pg_roles owner on owner.oid = c.relowner
                where c.relname = any($1::text[])
                """,
                list(knowledge_tables),
            )
            assert [row["rolname"] for row in owners] == ["flyway_owner"]

            with pytest.raises(asyncpg.ForeignKeyViolationError):
                await admin.execute(
                    """
                    insert into knowledge_chunk
                        (id, tenant_id, document_id, chunk_key, section, content,
                         content_hash, token_count, ordinal, status)
                    values
                        ('cccccccc-cccc-cccc-cccc-ccccccccccc2', $1, $2, 'cross-tenant',
                         'Synthetic', 'Synthetic only.', $3, 2, 1, 'ACTIVE')
                    """,
                    TENANT_B,
                    document_a,
                    "c" * 64,
                )
        finally:
            await admin.close()
    finally:
        await database.dispose()

    downgrade = await asyncio.to_thread(_run_alembic, migration_url, "downgrade", "base")
    assert downgrade.returncode == 0, downgrade.stdout + downgrade.stderr
    admin = await asyncpg.connect(admin_url.replace("+asyncpg", ""))
    try:
        assert await admin.fetchval("select to_regclass('public.knowledge_document')") is None
        assert await admin.fetchval("select to_regclass('public.work_order')") == "work_order"
        assert await admin.fetchval(
            "select extversion from pg_extension where extname = 'vector'"
        )
    finally:
        await admin.close()
