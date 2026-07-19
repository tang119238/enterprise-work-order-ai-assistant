from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from scripts import generate_smoke_fixtures


def test_generates_ephemeral_signed_tokens_env_and_idempotent_authority_sql(
    tmp_path: Path,
) -> None:
    paths = generate_smoke_fixtures.generate_fixtures(
        tmp_path,
        now=datetime(2026, 7, 18, 10, 0, tzinfo=UTC),
        lifetime_seconds=600,
        public_key_env_path=".smoke/jwt-public.pem",
    )

    assert paths.private_key.read_text(encoding="ascii").startswith(
        "-----BEGIN " + "PRIVATE KEY-----"
    )
    public_pem = paths.public_key.read_bytes()
    assert public_pem.startswith(b"-----BEGIN PUBLIC KEY-----")
    environment = read_env(paths.environment)
    sql = paths.provision_sql.read_text(encoding="utf-8")

    assert environment["SMOKE_JWT_PUBLIC_KEY_PATH"] == ".smoke/jwt-public.pem"
    assert environment["SMOKE_JWT_ISSUER"] == "http://smoke-issuer.local"
    assert environment["SMOKE_JWT_AUDIENCE"] == "work-order-service"
    assert environment["SMOKE_RUNTIME_DB_PASSWORD"] == "work_order_app_dev"

    dispatcher = verify_token(environment["SMOKE_DISPATCHER_TOKEN"], public_pem)
    ai = verify_token(environment["SMOKE_AI_TOKEN"], public_pem)
    tenant_b = verify_token(environment["SMOKE_TENANT_B_TOKEN"], public_pem)
    assert dispatcher["sub"] == ai["sub"]
    assert dispatcher["roles"] == ["DISPATCHER"]
    assert ai["roles"] == ["DISPATCHER", "AI_SERVICE"]
    assert tenant_b["tenant_id"] == "22222222-2222-2222-2222-222222222222"
    assert tenant_b["project_ids"] == ["00000000-0000-0000-0000-000000020001"]
    assert dispatcher["exp"] - dispatcher["nbf"] <= 900

    assert dispatcher["sub"] in sql
    assert tenant_b["sub"] in sql
    assert "'DISPATCHER'" in sql
    assert "'AI_SERVICE'" in sql
    assert "ON CONFLICT" in sql
    assert "SET LOCAL app.tenant_id" in sql
    assert environment["SMOKE_DISPATCHER_TOKEN"] not in sql
    assert "BEGIN PRIVATE KEY" not in sql


def test_smoke_secrets_are_ignored_and_compose_override_mounts_only_public_key() -> (
    None
):
    assert ".smoke/" in Path(".gitignore").read_text(encoding="utf-8")
    override = Path("docker-compose.smoke.yml").read_text(encoding="utf-8")
    assert "SMOKE_JWT_PUBLIC_KEY_PATH" in override
    assert "JWT_PUBLIC_KEY_LOCATION: file:/run/secrets/smoke-jwt-public.pem" in override
    assert "JWT_ISSUER_URI: ${SMOKE_JWT_ISSUER" in override
    assert "JWT_AUDIENCE: ${SMOKE_JWT_AUDIENCE" in override
    assert "private" not in override.lower()
    pyproject = Path("apps/ai-service/pyproject.toml").read_text(encoding="utf-8")
    assert '"cryptography>=45,<47"' in pyproject


def test_default_token_lifetime_is_exactly_900_seconds(tmp_path: Path) -> None:
    paths = generate_smoke_fixtures.generate_fixtures(
        tmp_path,
        now=datetime(2026, 7, 18, 10, 0, tzinfo=UTC),
    )
    environment = read_env(paths.environment)
    dispatcher = verify_token(
        environment["SMOKE_DISPATCHER_TOKEN"], paths.public_key.read_bytes()
    )

    assert dispatcher["exp"] - dispatcher["nbf"] == 900


def read_env(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )


def verify_token(token: str, public_pem: bytes) -> dict[str, object]:
    header_segment, claims_segment, signature_segment = token.split(".")
    header = decode_segment(header_segment)
    assert header == {"alg": "RS256", "typ": "JWT"}
    public_key = serialization.load_pem_public_key(public_pem)
    public_key.verify(
        decode_bytes(signature_segment),
        f"{header_segment}.{claims_segment}".encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return decode_segment(claims_segment)


def decode_segment(segment: str) -> dict[str, object]:
    value = json.loads(decode_bytes(segment))
    assert isinstance(value, dict)
    return value


def decode_bytes(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
