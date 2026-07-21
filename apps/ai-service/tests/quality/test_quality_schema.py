from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy import make_url
from testcontainers.core.docker_client import DockerClient
from testcontainers.postgres import PostgresContainer

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
AI_SERVICE_ROOT = REPOSITORY_ROOT / "apps" / "ai-service"
REVISION = AI_SERVICE_ROOT / "alembic" / "versions" / "20260718_02_quality_loop.py"
JAVA_MIGRATIONS = (
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


def _docker_available() -> bool:
    try:
        return bool(DockerClient().client.ping())
    except Exception:
        return False


DOCKER_AVAILABLE = _docker_available()


def _role_url(admin_url: str, username: str, password: str) -> str:
    return (
        make_url(admin_url)
        .set(username=username, password=password)
        .render_as_string(hide_password=False)
    )


def _run_alembic(migration_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["AI_MIGRATION_DATABASE_URL"] = migration_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
        cwd=AI_SERVICE_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _render_head() -> str:
    secret = "quality-offline-secret-never-print"
    result = _run_alembic(
        f"postgresql+asyncpg://flyway_owner:{secret}@db.invalid/workorders",
        "upgrade",
        "head",
        "--sql",
    )
    rendered = result.stdout + result.stderr
    assert result.returncode == 0, rendered
    assert secret not in rendered
    return rendered


def test_quality_revision_renders_owned_tables_constraints_and_tenant_indexes() -> None:
    assert REVISION.is_file()
    rendered = _render_head()

    created_tables = {
        line.split()[2] for line in rendered.splitlines() if line.startswith("CREATE TABLE ")
    }
    assert created_tables == {
        "alembic_version",
        "knowledge_document",
        "knowledge_chunk",
        "knowledge_embedding",
        "embedding_job",
        "quality_job",
        "model_call_audit",
        "quality_result",
        "quality_finding",
    }
    revision_source = REVISION.read_text(encoding="utf-8")
    assert "Revises: 20260718_01" in revision_source
    for column in (
        "work_order_version",
        "inspection_round",
        "business_key",
        "trigger_source",
        "trigger_payload",
        "priority",
        "retry_count",
        "max_retry_count",
        "next_retry_at",
        "started_at",
        "finished_at",
        "last_error_code",
        "last_error_message",
        "result_id",
        "work_order_snapshot",
        "policy_versions",
        "attachment_summary",
        "callback_state",
        "rule_code",
        "policy_chunk_id",
        "prompt_version",
        "raw_response_truncated",
    ):
        assert f'"{column}"' in revision_source
    assert "business_key VARCHAR(300) NOT NULL" in rendered
    assert (
        "CONSTRAINT uq_quality_job_tenant_business_key UNIQUE (tenant_id, business_key)" in rendered
    )
    assert "UNIQUE (tenant_id, work_order_id, work_order_version, inspection_round)" in rendered
    assert "CONSTRAINT uq_quality_result_job UNIQUE (quality_job_id)" in rendered
    assert (
        "status IN ('PENDING', 'RUNNING', 'RETRY_WAIT', 'SUCCEEDED', 'FAILED', 'SKIPPED')"
        in rendered
    )
    assert "verdict IN ('PASS', 'FAIL', 'UNCERTAIN', 'SKIP')" in rendered
    assert "severity IN ('LOW', 'MEDIUM', 'HIGH')" in rendered
    assert "label IN ('PASS', 'FAIL', 'UNCERTAIN', 'SKIP')" in rendered
    assert "source IN ('RULE', 'MODEL')" in rendered
    assert "TIMESTAMP WITH TIME ZONE" in rendered
    assert rendered.count("JSONB") >= 7
    assert "FOREIGN KEY(tenant_id, work_order_id) REFERENCES work_order (tenant_id, id)" in rendered
    assert (
        "FOREIGN KEY(tenant_id, quality_job_id) REFERENCES quality_job (tenant_id, id)" in rendered
    )
    assert (
        "FOREIGN KEY(tenant_id, quality_result_id) REFERENCES quality_result (tenant_id, id)"
        in rendered
    )
    assert (
        "CONSTRAINT fk_quality_job_result FOREIGN KEY(tenant_id, result_id) "
        "REFERENCES quality_result (tenant_id, id)" in rendered
    )
    for index in (
        "idx_quality_job_tenant_status_retry",
        "idx_quality_job_tenant_work_order",
        "idx_model_call_audit_tenant_job",
        "idx_quality_result_tenant_work_order",
        "idx_quality_finding_tenant_result",
    ):
        assert f"CREATE INDEX {index}" in rendered


def test_quality_tables_force_rls_and_expose_least_privilege_ai_writes() -> None:
    rendered = _render_head()

    for table in ("quality_job", "model_call_audit", "quality_result", "quality_finding"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in rendered
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in rendered
        assert f"CREATE POLICY {table}_tenant_policy ON {table}" in rendered
        assert (
            f"REVOKE ALL PRIVILEGES ON TABLE {table} "
            "FROM PUBLIC, work_order_app, analytics_reader" in rendered
        )
    assert "GRANT SELECT, INSERT, UPDATE ON TABLE quality_job TO ai_app" in rendered
    for table in ("model_call_audit", "quality_result", "quality_finding"):
        assert f"GRANT SELECT, INSERT ON TABLE {table} TO ai_app" in rendered
        assert f"GRANT SELECT, INSERT, UPDATE ON TABLE {table} TO ai_app" not in rendered
        assert f"GRANT UPDATE ON TABLE {table} TO ai_app" not in rendered
    assert "GRANT DELETE ON TABLE quality_" not in rendered
    assert "nullif(current_setting('app.tenant_id', true), '')::uuid" in rendered


async def _apply_java_migrations(migration_url: str) -> None:
    connection = await asyncpg.connect(migration_url.replace("+asyncpg", ""))
    try:
        migrations = sorted(
            JAVA_MIGRATIONS.glob("V*.sql"),
            key=lambda path: int(path.name[1:].split("__")[0]),
        )
        for migration in migrations:
            await connection.execute(migration.read_text(encoding="utf-8"))
    finally:
        await connection.close()


@pytest.fixture(scope="module")
def quality_database_urls() -> Iterator[dict[str, str]]:
    if not DOCKER_AVAILABLE:
        pytest.skip("Docker Engine unavailable; quality schema integration disabled")

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
        asyncio.run(_apply_java_migrations(migration_url))
        migration = _run_alembic(migration_url, "upgrade", "head")
        assert migration.returncode == 0, migration.stdout + migration.stderr
        yield {"admin": admin_url, "runtime": runtime_url}


@pytest.mark.asyncio
async def test_quality_business_key_is_unique_and_published_results_are_immutable(
    quality_database_urls: dict[str, str],
) -> None:
    admin = await asyncpg.connect(quality_database_urls["admin"].replace("+asyncpg", ""))
    try:
        work_order_id = await admin.fetchval(
            "select id from work_order where tenant_id=$1 order by work_order_no limit 1",
            TENANT_A,
        )
    finally:
        await admin.close()

    runtime = await asyncpg.connect(quality_database_urls["runtime"].replace("+asyncpg", ""))
    job_id = uuid4()
    result_id = uuid4()
    try:
        await runtime.execute("select set_config('app.tenant_id', $1, false)", str(TENANT_A))
        insert_job = """
            insert into quality_job
                (id, tenant_id, work_order_id, work_order_version, inspection_round, business_key,
                 trigger_source, status)
            values ($1, $2, $3, 0, 1, $4, 'WORK_ORDER_COMPLETED', 'PENDING')
        """
        await runtime.execute(insert_job, job_id, TENANT_A, work_order_id, "quality:test:one")
        with pytest.raises(asyncpg.UniqueViolationError):
            await runtime.execute(insert_job, uuid4(), TENANT_A, work_order_id, "quality:test:two")

        await runtime.execute(
            """
            insert into quality_result
                (id, tenant_id, quality_job_id, work_order_id, work_order_version,
                 inspection_round, verdict, confidence,
                 work_order_snapshot, policy_versions, attachment_summary)
            values ($1, $2, $3, $4, 0, 1, 'PASS', 0.95,
                    '{}'::jsonb, '[]'::jsonb, '{}'::jsonb)
            """,
            result_id,
            TENANT_A,
            job_id,
            work_order_id,
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.execute(
                "update quality_result set verdict='FAIL' where tenant_id=$1 and id=$2",
                TENANT_A,
                result_id,
            )
    finally:
        await runtime.close()
