import json
import os
import subprocess
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from app.config import Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
PYPROJECT = REPOSITORY_ROOT / "apps" / "ai-service" / "pyproject.toml"
DOCKERFILE = REPOSITORY_ROOT / "apps" / "ai-service" / "Dockerfile"
ENV_EXAMPLE = REPOSITORY_ROOT / ".env.example"


def parsed_compose() -> dict[str, object]:
    environment = os.environ.copy()
    environment.update(
        {
            "POSTGRES_PASSWORD": "postgres_dev",
            "FLYWAY_PASSWORD": "flyway_owner_dev",
            "WORK_ORDER_DB_PASSWORD": "work_order_app_dev",
            "AI_DB_PASSWORD": "ai_app_dev",
            "ANALYTICS_DB_PASSWORD": "analytics_reader_dev",
        }
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(REPOSITORY_ROOT / "docker-compose.yml"),
            "config",
            "--format",
            "json",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def dockerfile_instructions() -> list[tuple[str, str]]:
    logical_lines: list[str] = []
    pending = ""
    for raw_line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        pending = f"{pending} {line}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        logical_lines.append(pending)
        pending = ""

    assert not pending, "Dockerfile ends with an incomplete continuation"
    return [tuple(line.split(maxsplit=1)) for line in logical_lines]  # type: ignore[misc]


def example_environment() -> dict[str, str]:
    return dict(
        line.split("=", maxsplit=1)
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )


def test_settings_expose_canonical_pgvector_runtime_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.ai_database_url == (
        "postgresql+asyncpg://ai_app:ai_app_dev@localhost:5432/workorders"
    )
    assert settings.ai_migration_database_url == (
        "postgresql+asyncpg://flyway_owner:flyway_owner_dev@localhost:5432/workorders"
    )
    assert settings.embedding_provider == "local"
    assert settings.embedding_model == "BAAI/bge-small-zh-v1.5"
    assert settings.embedding_dimensions == 512
    assert settings.fastembed_cache_path == Path("/models")
    assert Settings.model_json_schema()["properties"]["embedding_dimensions"]["const"] == 512
    assert "ai_db_url" not in Settings.model_fields


@pytest.mark.parametrize("dimensions", [0, 384, 511, 513, 768])
def test_settings_reject_every_non_512_embedding_dimension(dimensions: int) -> None:
    with pytest.raises(ValidationError, match="512"):
        Settings(embedding_dimensions=dimensions, _env_file=None)


def test_canonical_environment_names_configure_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "AI_DATABASE_URL",
        "postgresql+asyncpg://ai_app:runtime_dev@db.internal:5432/workorders",
    )
    monkeypatch.setenv(
        "AI_MIGRATION_DATABASE_URL",
        "postgresql+asyncpg://flyway_owner:migration_dev@db.internal:5432/workorders",
    )
    monkeypatch.setenv("EMBEDDING_PROVIDER", "disabled")
    monkeypatch.setenv("EMBEDDING_MODEL", "synthetic/test-model")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "512")
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", "/tmp/model-cache")

    settings = Settings(_env_file=None)

    assert settings.ai_database_url.endswith("@db.internal:5432/workorders")
    assert settings.ai_migration_database_url.startswith("postgresql+asyncpg://flyway_owner:")
    assert settings.embedding_provider == "disabled"
    assert settings.embedding_model == "synthetic/test-model"
    assert settings.embedding_dimensions == 512
    assert settings.fastembed_cache_path == Path("/tmp/model-cache")


def test_environment_rejects_non_512_embedding_dimension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "511")

    with pytest.raises(ValidationError, match="512"):
        Settings(_env_file=None)


def test_compose_uses_internal_pgvector_database_and_preserves_bootstrap() -> None:
    compose = parsed_compose()
    postgres = compose["services"]["postgres"]  # type: ignore[index]

    assert postgres["image"] == "pgvector/pgvector:pg16"
    assert "ports" not in postgres
    assert postgres["healthcheck"]["test"] == [
        "CMD-SHELL",
        "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB",
    ]
    mounts = {(mount["source"], mount["target"]) for mount in postgres["volumes"]}
    assert ("work-order-data", "/var/lib/postgresql/data") in mounts
    assert any(target == "/docker-entrypoint-initdb.d" for _, target in mounts)


def test_compose_separates_ai_runtime_and_migration_credentials() -> None:
    compose = parsed_compose()
    ai_service = compose["services"]["ai-service"]  # type: ignore[index]
    environment = ai_service["environment"]

    runtime_url = urlsplit(environment["AI_DATABASE_URL"])
    migration_url = urlsplit(environment["AI_MIGRATION_DATABASE_URL"])
    assert runtime_url.username == "ai_app"
    assert migration_url.username == "flyway_owner"
    assert runtime_url.hostname == migration_url.hostname == "postgres"
    assert "AI_DB_URL" not in environment
    assert "AI_DB_USERNAME" not in environment
    assert "AI_DB_PASSWORD" not in environment
    assert compose["services"]["work-order-service"]["environment"]["DB_USERNAME"] == (
        "work_order_app"
    )


def test_runtime_application_does_not_consume_migration_database_url() -> None:
    application_root = REPOSITORY_ROOT / "apps" / "ai-service" / "app"
    consumers = [
        path.relative_to(application_root)
        for path in application_root.rglob("*.py")
        if path.name != "config.py"
        and "ai_migration_database_url" in path.read_text(encoding="utf-8")
    ]

    assert consumers == []


def test_compose_mounts_named_fastembed_cache_only_at_runtime() -> None:
    compose = parsed_compose()
    ai_service = compose["services"]["ai-service"]  # type: ignore[index]
    mounts = [mount for mount in ai_service["volumes"] if mount["target"] == "/models"]

    assert mounts == [
        {
            "type": "volume",
            "source": "fastembed-cache",
            "target": "/models",
            "volume": {},
        }
    ]
    assert "fastembed-cache" in compose["volumes"]
    assert ai_service["environment"]["FASTEMBED_CACHE_PATH"] == "/models"


def test_env_example_documents_synthetic_persistence_and_embedding_settings() -> None:
    environment = example_environment()
    runtime_url = urlsplit(environment["AI_DATABASE_URL"])
    migration_url = urlsplit(environment["AI_MIGRATION_DATABASE_URL"])

    assert (runtime_url.username, runtime_url.hostname) == ("ai_app", "127.0.0.1")
    assert (migration_url.username, migration_url.hostname) == ("flyway_owner", "127.0.0.1")
    assert environment["EMBEDDING_PROVIDER"] == "local"
    assert environment["EMBEDDING_MODEL"] == "BAAI/bge-small-zh-v1.5"
    assert environment["EMBEDDING_DIMENSIONS"] == "512"
    assert environment["FASTEMBED_CACHE_PATH"] == "/models"


def test_pyproject_declares_persistence_runtime_and_test_dependencies() -> None:
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    runtime = set(pyproject["project"]["dependencies"])
    dev = set(pyproject["project"]["optional-dependencies"]["dev"])

    assert {
        "sqlalchemy[asyncio]>=2.0,<3",
        "asyncpg>=0.30,<1",
        "alembic>=1.15,<2",
        "pgvector>=0.4,<1",
        "fastembed>=0.7,<1",
    } <= runtime
    assert "testcontainers[postgres]>=4.10,<5" in dev
    assert "cryptography>=45,<47" in dev


def test_dockerfile_installs_project_without_baking_model_weights() -> None:
    instructions = dockerfile_instructions()
    run_commands = [
        argument for instruction, argument in instructions if instruction.upper() == "RUN"
    ]
    copy_sources = [
        argument for instruction, argument in instructions if instruction.upper() == "COPY"
    ]
    dockerfile = DOCKERFILE.read_text(encoding="utf-8").lower()

    assert any("python -m pip install ." in command for command in run_commands)
    assert any("mkdir -p /models" in command for command in run_commands)
    assert any("chown app:app /models" in command for command in run_commands)
    assert all("/models" not in copy_source for copy_source in copy_sources)
    assert "snapshot_download" not in dockerfile
    assert "huggingface-cli" not in dockerfile
    assert "fastembed.textembedding" not in dockerfile
    assert "wget " not in dockerfile
    assert "curl " not in dockerfile
