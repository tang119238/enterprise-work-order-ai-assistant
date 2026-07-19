from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import make_url

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
AI_SERVICE_ROOT = REPOSITORY_ROOT / "apps" / "ai-service"
ALEMBIC_INI = AI_SERVICE_ROOT / "alembic.ini"
ALEMBIC_ENV = AI_SERVICE_ROOT / "alembic" / "env.py"
REVISION = AI_SERVICE_ROOT / "alembic" / "versions" / "20260718_01_knowledge_pgvector.py"
ROLE_BOOTSTRAP = REPOSITORY_ROOT / "infra" / "postgres" / "init" / "001_roles.sql"
VECTOR_BOOTSTRAP = REPOSITORY_ROOT / "infra" / "postgres" / "init" / "000_pgvector.sql"
README = REPOSITORY_ROOT / "README.md"


def test_alembic_and_admin_extension_bootstrap_assets_exist() -> None:
    assert ALEMBIC_INI.is_file()
    assert ALEMBIC_ENV.is_file()
    assert REVISION.is_file()
    assert VECTOR_BOOTSTRAP.is_file()


def test_role_bootstrap_keeps_migration_and_runtime_roles_unprivileged() -> None:
    role_sql = ROLE_BOOTSTRAP.read_text(encoding="utf-8").upper()
    vector_sql = VECTOR_BOOTSTRAP.read_text(encoding="utf-8").strip()

    assert "CREATE EXTENSION IF NOT EXISTS vector" in vector_sql
    assert "PASSWORD" not in vector_sql.upper()
    assert "SUPERUSER" not in role_sql
    assert "BYPASSRLS" not in role_sql
    assert "GRANT USAGE, CREATE ON SCHEMA PUBLIC TO FLYWAY_OWNER" in role_sql
    assert "GRANT USAGE ON SCHEMA PUBLIC TO WORK_ORDER_APP, AI_APP, ANALYTICS_READER" in role_sql


def test_offline_migration_renders_complete_fail_closed_schema_without_secrets() -> None:
    secret = "offline-secret-never-print"
    environment = os.environ.copy()
    environment["AI_MIGRATION_DATABASE_URL"] = (
        f"postgresql+asyncpg://flyway_owner:{secret}@db.example.invalid:5432/workorders"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            "20260718_01",
            "--sql",
        ],
        cwd=AI_SERVICE_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    rendered = result.stdout + result.stderr

    assert result.returncode == 0, rendered
    assert secret not in rendered
    assert "CREATE EXTENSION IF NOT EXISTS vector" in rendered
    created_tables = {
        line.split()[2] for line in rendered.splitlines() if line.startswith("CREATE TABLE ")
    }
    assert created_tables == {
        "alembic_version",
        "knowledge_document",
        "knowledge_chunk",
        "knowledge_embedding",
        "embedding_job",
    }
    assert rendered.count("PRIMARY KEY (tenant_id, id)") == 3
    assert "UNIQUE (tenant_id, id)" not in rendered
    expected_foreign_keys = {
        "fk_knowledge_document_tenant": "FOREIGN KEY(tenant_id) REFERENCES tenant (id)",
        "fk_knowledge_chunk_tenant_document": (
            "FOREIGN KEY(tenant_id, document_id) "
            "REFERENCES knowledge_document (tenant_id, id) ON DELETE CASCADE"
        ),
        "fk_knowledge_embedding_tenant_chunk": (
            "FOREIGN KEY(tenant_id, chunk_id) "
            "REFERENCES knowledge_chunk (tenant_id, id) ON DELETE CASCADE"
        ),
        "fk_embedding_job_tenant_document": (
            "FOREIGN KEY(tenant_id, document_id) "
            "REFERENCES knowledge_document (tenant_id, id) ON DELETE CASCADE"
        ),
        "fk_embedding_job_tenant_document_chunk": (
            "FOREIGN KEY(tenant_id, document_id, chunk_id) "
            "REFERENCES knowledge_chunk (tenant_id, document_id, id) ON DELETE CASCADE"
        ),
    }
    for name, definition in expected_foreign_keys.items():
        assert f"CONSTRAINT {name} {definition}" in rendered
    assert "DEFERRABLE" not in rendered
    for table in (
        "knowledge_document",
        "knowledge_chunk",
        "knowledge_embedding",
        "embedding_job",
    ):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in rendered
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in rendered
        assert "nullif(current_setting('app.tenant_id', true), '')::uuid" in rendered
    assert "VECTOR(512)" in rendered.upper()
    assert "vector_cosine_ops" in rendered
    assert "m = 16" in rendered
    assert "ef_construction = 64" in rendered
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE" in rendered
    assert " TO ai_app" in rendered
    assert "FROM PUBLIC, work_order_app, analytics_reader" in rendered
    assert "GRANT" not in "\n".join(
        line for line in rendered.splitlines() if "work_order" in line.lower()
    )
    assert "DROP EXTENSION" not in rendered.upper()


def test_migration_subprocesses_use_the_active_python_interpreter_portably() -> None:
    migration_tests = (
        Path(__file__),
        Path(__file__).with_name("test_pgvector_integration.py"),
    )
    windows_venv_fragment = ".venv/" + '" / "' + "Scripts"
    python_exe_fragment = "Scripts" + '" / "' + "python.exe"

    assert Path(sys.executable).is_file()
    for test_file in migration_tests:
        source = test_file.read_text(encoding="utf-8").replace("\\", "/")
        assert windows_venv_fragment not in source
        assert python_exe_fragment not in source


def test_external_database_bootstrap_and_migration_commands_are_actionable() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "PGVECTOR_ADMIN_DATABASE_URL" in readme
    assert "python -m app.pgvector_bootstrap" in readme
    assert "AI_MIGRATION_DATABASE_URL" in readme
    assert "python -m alembic -c alembic.ini upgrade head" in readme
    assert "CREATE EXTENSION IF NOT EXISTS vector" in readme


@pytest.mark.asyncio
async def test_database_uses_only_runtime_settings_and_disposes_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import Settings
    from app.db import Database

    runtime_url = "postgresql+asyncpg://ai_app:runtime-secret@runtime.invalid/workorders"
    monkeypatch.setenv("AI_DATABASE_URL", runtime_url)
    monkeypatch.setenv(
        "AI_MIGRATION_DATABASE_URL",
        "postgresql+asyncpg://flyway_owner:migration-secret@migration.invalid/workorders",
    )

    database = Database(Settings(_env_file=None))
    assert make_url(str(database._engine.url)).host == "runtime.invalid"
    await database.dispose()
    await database.dispose()


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def execute(self, statement: object, parameters: dict[str, str]) -> None:
        self.calls.append((str(statement), parameters))


class _FakeTransaction:
    def __init__(self, session: _FakeSession, events: list[str]) -> None:
        self._session = session
        self._events = events

    async def __aenter__(self) -> _FakeSession:
        self._events.append("begin")
        return self._session

    async def __aexit__(self, *_: object) -> None:
        self._events.append("end")


class _FakeSessionFactory:
    def __init__(self, session: _FakeSession, events: list[str]) -> None:
        self._session = session
        self._events = events

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction(self._session, self._events)


class _FakeEngine:
    def __init__(self) -> None:
        self.dispose_calls = 0

    async def dispose(self) -> None:
        self.dispose_calls += 1


@pytest.mark.asyncio
async def test_database_session_sets_transaction_local_tenant_before_yielding() -> None:
    from app.db import Database

    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    session = _FakeSession()
    events: list[str] = []
    database = object.__new__(Database)
    database._engine = _FakeEngine()
    database._session_factory = _FakeSessionFactory(session, events)
    database._closed = False

    async with database.session(tenant_id) as yielded:
        assert yielded is session
        assert events == ["begin"]
        assert session.calls == [
            (
                "select set_config('app.tenant_id', :tenant_id, true)",
                {"tenant_id": str(tenant_id)},
            )
        ]

    assert events == ["begin", "end"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant_id", [None, "11111111-1111-1111-1111-111111111111"])
async def test_database_rejects_unscoped_or_non_uuid_sessions(tenant_id: object) -> None:
    from app.db import Database

    session = _FakeSession()
    events: list[str] = []
    database = object.__new__(Database)
    database._engine = _FakeEngine()
    database._session_factory = _FakeSessionFactory(session, events)
    database._closed = False

    with pytest.raises((TypeError, ValueError), match="tenant_id"):
        async with database.session(tenant_id):  # type: ignore[arg-type]
            pytest.fail("invalid tenant scope must never yield query access")

    assert events == []
    assert session.calls == []
