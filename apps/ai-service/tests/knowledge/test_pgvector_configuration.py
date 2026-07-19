import json
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.config import Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
PYPROJECT = REPOSITORY_ROOT / "apps" / "ai-service" / "pyproject.toml"
DOCKERFILE = REPOSITORY_ROOT / "apps" / "ai-service" / "Dockerfile"
ENV_EXAMPLE = REPOSITORY_ROOT / ".env.example"
APPLICATION_YML = (
    REPOSITORY_ROOT
    / "apps"
    / "work-order-service"
    / "src"
    / "main"
    / "resources"
    / "application.yml"
)


def parsed_compose(overrides: dict[str, str] | None = None) -> dict[str, object]:
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
    environment.update(overrides or {})
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


def run_command_tokens(argument: str) -> list[list[str]]:
    return [shlex.split(command.strip()) for command in argument.split("&&")]


def example_environment() -> dict[str, str]:
    return dict(
        line.split("=", maxsplit=1)
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )


def test_settings_expose_canonical_pgvector_runtime_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.ai_database_url == (
        "postgresql+asyncpg://ai_app:ai_app_dev@postgres:5432/workorders"
    )
    assert settings.embedding_provider == "local"
    assert settings.embedding_model == "BAAI/bge-small-zh-v1.5"
    assert settings.embedding_dimensions == 512
    assert settings.fastembed_cache_path == Path("/models")
    assert Settings.model_json_schema()["properties"]["embedding_dimensions"]["const"] == 512
    assert "ai_db_url" not in Settings.model_fields
    assert "ai_migration_database_url" not in Settings.model_fields


@pytest.mark.parametrize("dimensions", [0, 384, 511, 513, 768])
def test_settings_reject_every_non_512_embedding_dimension(dimensions: int) -> None:
    with pytest.raises(ValidationError, match="512"):
        Settings(embedding_dimensions=dimensions, _env_file=None)


def test_canonical_environment_names_configure_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "AI_DATABASE_URL",
        "postgresql+asyncpg://ai_app:runtime_dev@db.internal:5432/workorders",
    )
    monkeypatch.setenv("EMBEDDING_PROVIDER", "disabled")
    monkeypatch.setenv("EMBEDDING_MODEL", "synthetic/test-model")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "512")
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", "/tmp/model-cache")

    settings = Settings(_env_file=None)

    assert settings.ai_database_url.endswith("@db.internal:5432/workorders")
    assert settings.embedding_provider == "disabled"
    assert settings.embedding_model == "synthetic/test-model"
    assert settings.embedding_dimensions == 512
    assert settings.fastembed_cache_path == Path("/tmp/model-cache")


def test_migration_settings_are_separate_and_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.migration_config import MigrationSettings

    migration_url = (
        "postgresql+asyncpg://flyway_owner:slash%2Fat%40hash%23@postgres:5432/workorders"
    )
    monkeypatch.setenv("AI_MIGRATION_DATABASE_URL", migration_url)

    runtime_settings = Settings(_env_file=None)
    migration_settings = MigrationSettings(_env_file=None)
    parsed_url = make_url(migration_settings.ai_migration_database_url)

    assert not hasattr(runtime_settings, "ai_migration_database_url")
    assert migration_settings.ai_migration_database_url == migration_url
    assert parsed_url.username == "flyway_owner"
    assert parsed_url.password == "slash/at@hash#"
    assert parsed_url.host == "postgres"
    assert parsed_url.database == "workorders"

    monkeypatch.delenv("AI_MIGRATION_DATABASE_URL")
    with pytest.raises(ValidationError, match="AI_MIGRATION_DATABASE_URL"):
        MigrationSettings(_env_file=None)


def test_pgvector_admin_settings_are_isolated_and_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.pgvector_bootstrap import PgvectorBootstrapSettings

    admin_url = "postgresql+asyncpg://postgres:admin-test@postgres:5432/workorders"
    monkeypatch.setenv("PGVECTOR_ADMIN_DATABASE_URL", admin_url)

    runtime_settings = Settings(_env_file=None)
    bootstrap_settings = PgvectorBootstrapSettings(_env_file=None)

    assert not hasattr(runtime_settings, "pgvector_admin_database_url")
    assert bootstrap_settings.pgvector_admin_database_url == admin_url

    monkeypatch.delenv("PGVECTOR_ADMIN_DATABASE_URL")
    with pytest.raises(ValidationError, match="PGVECTOR_ADMIN_DATABASE_URL"):
        PgvectorBootstrapSettings(_env_file=None)


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
        "pg_isready -h 127.0.0.1 -U $$POSTGRES_USER -d $$POSTGRES_DB",
    ]
    mounts = {(mount["source"], mount["target"]) for mount in postgres["volumes"]}
    assert ("work-order-data", "/var/lib/postgresql/data") in mounts
    assert any(target == "/docker-entrypoint-initdb.d" for _, target in mounts)


def test_compose_accepts_percent_encoded_runtime_url_without_raw_password_interpolation() -> None:
    raw_password = "slash/at@hash#"
    canonical_url = (
        "postgresql+asyncpg://ai_app:slash%2Fat%40hash%23@postgres:5432/workorders"
    )
    compose = parsed_compose(
        {
            "AI_DB_PASSWORD": raw_password,
            "AI_DATABASE_URL": canonical_url,
        }
    )
    ai_service = compose["services"]["ai-service"]  # type: ignore[index]
    environment = ai_service["environment"]

    runtime_url = make_url(environment["AI_DATABASE_URL"])
    assert environment["AI_DATABASE_URL"] == canonical_url
    assert runtime_url.username == "ai_app"
    assert runtime_url.password == raw_password
    assert runtime_url.host == "postgres"
    assert runtime_url.database == "workorders"
    assert compose["services"]["postgres"]["environment"]["AI_DB_PASSWORD"] == raw_password
    assert "AI_DB_URL" not in environment
    assert "AI_DB_USERNAME" not in environment
    assert "AI_DB_PASSWORD" not in environment
    assert "AI_MIGRATION_DATABASE_URL" not in environment
    migration_consumers = {
        name
        for name, service in compose["services"].items()
        if "AI_MIGRATION_DATABASE_URL" in service.get("environment", {})
    }
    assert migration_consumers == {"ai-migrate"}
    assert compose["services"]["work-order-service"]["environment"]["DB_USERNAME"] == (
        "work_order_app"
    )


def test_compose_runs_short_lived_admin_bootstrap_then_alembic_before_ai() -> None:
    compose = parsed_compose()
    services = compose["services"]  # type: ignore[index]
    bootstrap = services["pgvector-bootstrap"]
    work_order_migration = services["work-order-migrate"]
    migration = services["ai-migrate"]
    runtime = services["ai-service"]
    work_order_runtime = services["work-order-service"]
    postgres = services["postgres"]

    assert bootstrap["restart"] == "no"
    assert bootstrap["command"] == ["python", "-m", "app.pgvector_bootstrap"]
    assert set(bootstrap["environment"]) == {"PGVECTOR_ADMIN_DATABASE_URL"}
    assert bootstrap["depends_on"]["postgres"]["condition"] == "service_healthy"

    assert work_order_migration["image"] == "flyway/flyway:10.20.1"
    assert work_order_migration["restart"] == "no"
    assert work_order_migration["command"] == ["migrate"]
    assert set(work_order_migration["environment"]) == {
        "FLYWAY_URL",
        "FLYWAY_USER",
        "FLYWAY_PASSWORD",
    }
    assert work_order_migration["environment"]["FLYWAY_URL"] == (
        "jdbc:postgresql://postgres:5432/workorders"
    )
    assert work_order_migration["environment"]["FLYWAY_USER"] == "flyway_owner"
    assert work_order_migration["depends_on"]["pgvector-bootstrap"]["condition"] == (
        "service_completed_successfully"
    )
    migration_mounts = [
        mount
        for mount in work_order_migration["volumes"]
        if mount["target"] == "/flyway/sql"
    ]
    assert len(migration_mounts) == 1
    assert migration_mounts[0]["type"] == "bind"
    assert migration_mounts[0]["read_only"] is True
    assert Path(migration_mounts[0]["source"]).resolve() == (
        REPOSITORY_ROOT
        / "apps"
        / "work-order-service"
        / "src"
        / "main"
        / "resources"
        / "db"
        / "migration"
    ).resolve()

    assert work_order_runtime["depends_on"]["work-order-migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert set(work_order_runtime["depends_on"]) == {"work-order-migrate"}
    assert work_order_runtime["environment"]["SPRING_FLYWAY_ENABLED"] == "false"
    assert work_order_runtime["environment"]["DB_USERNAME"] == "work_order_app"
    assert "FLYWAY_USER" not in work_order_runtime["environment"]
    assert "FLYWAY_PASSWORD" not in work_order_runtime["environment"]

    assert migration["restart"] == "no"
    assert migration["command"] == [
        "python",
        "-m",
        "alembic",
        "-c",
        "alembic.ini",
        "upgrade",
        "head",
    ]
    assert set(migration["environment"]) == {"AI_MIGRATION_DATABASE_URL"}
    assert set(migration["depends_on"]) == {"work-order-migrate"}
    assert migration["depends_on"]["work-order-migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert runtime["depends_on"]["ai-migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert runtime["depends_on"]["work-order-service"]["condition"] == "service_healthy"
    assert set(runtime["depends_on"]) == {"ai-migrate", "work-order-service"}

    assert "PGVECTOR_ADMIN_DATABASE_URL" not in runtime["environment"]
    assert "AI_MIGRATION_DATABASE_URL" not in runtime["environment"]
    assert "PGVECTOR_ADMIN_DATABASE_URL" not in postgres["environment"]
    assert "AI_MIGRATION_DATABASE_URL" not in postgres["environment"]

    elevated_keys = {
        "PGVECTOR_ADMIN_DATABASE_URL",
        "AI_MIGRATION_DATABASE_URL",
        "FLYWAY_USER",
        "FLYWAY_PASSWORD",
        "FLYWAY_URL",
    }
    assert elevated_keys.isdisjoint(runtime["environment"])
    assert elevated_keys.isdisjoint(work_order_runtime["environment"])


def test_postgres_readiness_rejects_the_temporary_init_socket() -> None:
    compose = parsed_compose()
    probe = compose["services"]["postgres"]["healthcheck"]["test"]  # type: ignore[index]
    command = probe[1]

    assert probe[0] == "CMD-SHELL"
    assert "pg_isready" in command
    assert "-h 127.0.0.1" in command
    assert command != "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"


def test_spring_flyway_defaults_remain_enabled_outside_compose() -> None:
    application = APPLICATION_YML.read_text(encoding="utf-8")

    assert "flyway:" in application
    assert "enabled: true" in application
    assert "user: ${FLYWAY_USER:flyway_owner}" in application
    assert "password: ${FLYWAY_PASSWORD:}" in application


def test_runtime_application_does_not_consume_migration_database_url() -> None:
    application_root = REPOSITORY_ROOT / "apps" / "ai-service" / "app"
    consumers = [
        path.relative_to(application_root)
        for path in application_root.rglob("*.py")
        if path.name != "migration_config.py"
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
    runtime_url = make_url(environment["AI_DATABASE_URL"])
    migration_url = make_url(environment["AI_MIGRATION_DATABASE_URL"])
    admin_url = make_url(environment["PGVECTOR_ADMIN_DATABASE_URL"])
    example_text = ENV_EXAMPLE.read_text(encoding="utf-8").lower()

    assert (runtime_url.username, runtime_url.host) == ("ai_app", "postgres")
    assert (migration_url.username, migration_url.host) == ("flyway_owner", "postgres")
    assert (admin_url.username, admin_url.host) == ("postgres", "postgres")
    assert "percent-encode" in example_text
    assert "no host database port" in example_text
    assert "db.example.invalid" in example_text
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
    indexed_runs = [
        (index, argument)
        for index, (instruction, argument) in enumerate(instructions)
        if instruction.upper() == "RUN"
    ]
    tokenized_runs = [
        (index, tokens)
        for index, argument in indexed_runs
        for tokens in run_command_tokens(argument)
    ]
    copy_sources = [
        argument for instruction, argument in instructions if instruction.upper() == "COPY"
    ]
    dockerfile = DOCKERFILE.read_text(encoding="utf-8").lower()

    pip_installs = [
        tokens
        for _, tokens in tokenized_runs
        if tokens[:4] == ["python", "-m", "pip", "install"]
    ]
    project_installs = [
        tokens
        for tokens in pip_installs
        if any(token.startswith(".") for token in tokens[4:])
    ]
    assert project_installs == [["python", "-m", "pip", "install", "."]]
    assert all(".[dev]" not in tokens for tokens in project_installs)
    assert all("--group" not in tokens and "--extra" not in tokens for tokens in pip_installs)

    user_index = next(
        index
        for index, (instruction, argument) in enumerate(instructions)
        if instruction.upper() == "USER" and argument == "app"
    )
    cache_run_index, cache_tokens = next(
        (index, tokens)
        for index, tokens in tokenized_runs
        if tokens[:3] == ["mkdir", "-p", "/models"]
    )
    chown_run_index, chown_tokens = next(
        (index, tokens)
        for index, tokens in tokenized_runs
        if tokens[:3] == ["chown", "app:app", "/models"]
    )
    assert cache_run_index == chown_run_index < user_index
    assert cache_tokens == ["mkdir", "-p", "/models"]
    assert chown_tokens == ["chown", "app:app", "/models"]
    assert all("/models" not in copy_source for copy_source in copy_sources)
    assert "apps/ai-service/alembic.ini ./alembic.ini" in copy_sources
    assert "apps/ai-service/alembic ./alembic" in copy_sources
    assert "snapshot_download" not in dockerfile
    assert "huggingface-cli" not in dockerfile
    assert "fastembed.textembedding" not in dockerfile
    assert "wget " not in dockerfile
    assert "curl " not in dockerfile


def test_pgvector_bootstrap_failure_is_actionable_and_secret_safe() -> None:
    marker = "bootstrap-secret-never-print"
    environment = os.environ.copy()
    environment["PGVECTOR_ADMIN_DATABASE_URL"] = f"not-a-database-url-{marker}"
    result = subprocess.run(
        [sys.executable, "-m", "app.pgvector_bootstrap"],
        cwd=REPOSITORY_ROOT / "apps" / "ai-service",
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert marker not in output
    assert "CREATE EXTENSION IF NOT EXISTS vector" in output
    assert "PGVECTOR_ADMIN_DATABASE_URL" in output


@pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_VOLUME_TESTS") != "1",
    reason="requires Docker Engine and AI_SERVICE_TEST_IMAGE",
)
def test_runtime_user_can_write_a_fresh_fastembed_volume() -> None:
    image = os.environ.get("AI_SERVICE_TEST_IMAGE")
    assert image, "AI_SERVICE_TEST_IMAGE is required when RUN_DOCKER_VOLUME_TESTS=1"
    volume_name = f"ai-fastembed-cache-test-{uuid4().hex}"

    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--volume",
                f"{volume_name}:/models",
                image,
                "sh",
                "-c",
                "test -w /models && touch /models/write-probe",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
    finally:
        subprocess.run(
            ["docker", "volume", "rm", volume_name],
            check=False,
            capture_output=True,
            text=True,
        )
