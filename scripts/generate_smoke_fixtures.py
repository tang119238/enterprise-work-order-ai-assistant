from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
PROJECT_A = "00000000-0000-0000-0000-000000010001"
PROJECT_B = "00000000-0000-0000-0000-000000020001"


@dataclass(frozen=True)
class FixturePaths:
    private_key: Path
    public_key: Path
    environment: Path
    provision_sql: Path


def generate_fixtures(
    output_dir: Path,
    *,
    now: datetime | None = None,
    lifetime_seconds: int = 900,
    issuer: str = "http://smoke-issuer.local",
    audience: str = "work-order-service",
    public_key_env_path: str | None = None,
    runtime_db_password: str = "work_order_app_dev",
) -> FixturePaths:
    if not 60 <= lifetime_seconds <= 900:
        raise ValueError("Token lifetime must be between 60 and 900 seconds")
    if not issuer.strip() or not audience.strip():
        raise ValueError("Issuer and audience must be nonblank")
    instant = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    output_dir.mkdir(parents=True, exist_ok=True)
    private_path = output_dir / "jwt-private.pem"
    public_path = output_dir / "jwt-public.pem"
    env_path = output_dir / "smoke.env"
    sql_path = output_dir / "provision.sql"

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    run_id = uuid.uuid4().hex[:12]
    dispatcher_subject = f"synthetic-smoke-dispatcher-{run_id}"
    tenant_b_subject = f"synthetic-smoke-tenant-b-{run_id}"
    nbf = int((instant - timedelta(seconds=5)).timestamp())
    exp = nbf + lifetime_seconds
    common = {"iss": issuer, "aud": [audience], "nbf": nbf, "exp": exp}
    dispatcher = _sign(
        private_key,
        {
            **common,
            "sub": dispatcher_subject,
            "tenant_id": TENANT_A,
            "roles": ["DISPATCHER"],
            "project_ids": [PROJECT_A],
            "scope": "work-order:read work-order:write",
            "request_id": f"smoke-request-{run_id}",
            "trace_id": f"smoke-trace-{run_id}",
        },
    )
    ai = _sign(
        private_key,
        {
            **common,
            "sub": dispatcher_subject,
            "tenant_id": TENANT_A,
            "roles": ["DISPATCHER", "AI_SERVICE"],
            "project_ids": [PROJECT_A],
            "scope": "work-order:read work-order:write",
            "request_id": f"smoke-ai-request-{run_id}",
            "trace_id": f"smoke-ai-trace-{run_id}",
        },
    )
    tenant_b = _sign(
        private_key,
        {
            **common,
            "sub": tenant_b_subject,
            "tenant_id": TENANT_B,
            "roles": ["DISPATCHER"],
            "project_ids": [PROJECT_B],
            "scope": "work-order:read work-order:write",
            "request_id": f"smoke-b-request-{run_id}",
            "trace_id": f"smoke-b-trace-{run_id}",
        },
    )

    public_env = public_key_env_path or public_path.resolve().as_posix()
    environment = "\n".join(
        [
            "# Generated synthetic smoke credentials. Do not commit this file.",
            f"SMOKE_JWT_ISSUER={issuer}",
            f"SMOKE_JWT_AUDIENCE={audience}",
            f"SMOKE_JWT_PUBLIC_KEY_PATH={public_env}",
            f"SMOKE_DISPATCHER_TOKEN={dispatcher}",
            f"SMOKE_AI_TOKEN={ai}",
            f"SMOKE_TENANT_B_TOKEN={tenant_b}",
            "SMOKE_RUNTIME_DB_USER=work_order_app",
            f"SMOKE_RUNTIME_DB_PASSWORD={runtime_db_password}",
            f"SMOKE_PROVISION_SQL={sql_path.resolve().as_posix()}",
            "",
        ]
    )
    sql = _provision_sql(
        issuer=issuer,
        dispatcher_subject=dispatcher_subject,
        tenant_b_subject=tenant_b_subject,
    )

    _write(private_path, private_pem, secret=True)
    _write(public_path, public_pem)
    _write(env_path, environment.encode("utf-8"), secret=True)
    _write(sql_path, sql.encode("utf-8"))
    return FixturePaths(private_path, public_path, env_path, sql_path)


def _sign(private_key: rsa.RSAPrivateKey, claims: dict[str, object]) -> str:
    header_segment = _encode_json({"alg": "RS256", "typ": "JWT"})
    claims_segment = _encode_json(claims)
    signing_input = f"{header_segment}.{claims_segment}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header_segment}.{claims_segment}.{_encode_bytes(signature)}"


def _encode_json(value: dict[str, object]) -> str:
    return _encode_bytes(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )


def _encode_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _write(path: Path, content: bytes, *, secret: bool = False) -> None:
    path.write_bytes(content)
    if secret:
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)


def _sql(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _provision_sql(
    *, issuer: str, dispatcher_subject: str, tenant_b_subject: str
) -> str:
    dispatcher_user_id = str(uuid.uuid4())
    tenant_b_user_id = str(uuid.uuid4())
    values = {
        "issuer": _sql(issuer),
        "dispatcher": _sql(dispatcher_subject),
        "tenant_b": _sql(tenant_b_subject),
        "dispatcher_user_id": _sql(dispatcher_user_id),
        "tenant_b_user_id": _sql(tenant_b_user_id),
    }
    return f"""\
-- Generated synthetic-only smoke authorities. Safe to run repeatedly.
BEGIN;
SET LOCAL app.tenant_id = '{TENANT_A}';
INSERT INTO user_identity (id, issuer, subject, display_name, status)
VALUES ({values["dispatcher_user_id"]}::uuid, {values["issuer"]}, {values["dispatcher"]},
        'Synthetic Smoke Dispatcher', 'ACTIVE')
ON CONFLICT (issuer, subject) DO UPDATE
SET display_name = EXCLUDED.display_name, status = 'ACTIVE', updated_at = CURRENT_TIMESTAMP;
INSERT INTO tenant_membership (id, tenant_id, user_identity_id, role, status)
SELECT gen.id, '{TENANT_A}'::uuid, identity.id, gen.role, 'ACTIVE'
FROM user_identity identity
CROSS JOIN (VALUES
    ('{uuid.uuid4()}'::uuid, 'DISPATCHER'),
    ('{uuid.uuid4()}'::uuid, 'AI_SERVICE')
) AS gen(id, role)
WHERE identity.issuer = {values["issuer"]} AND identity.subject = {values["dispatcher"]}
ON CONFLICT (tenant_id, user_identity_id, role) DO UPDATE
SET status = 'ACTIVE', updated_at = CURRENT_TIMESTAMP;
INSERT INTO project_scope (id, tenant_id, user_identity_id, project_id, status)
SELECT '{uuid.uuid4()}'::uuid, '{TENANT_A}'::uuid, identity.id, '{PROJECT_A}'::uuid, 'ACTIVE'
FROM user_identity identity
WHERE identity.issuer = {values["issuer"]} AND identity.subject = {values["dispatcher"]}
ON CONFLICT (tenant_id, user_identity_id, project_id) DO UPDATE
SET status = 'ACTIVE', updated_at = CURRENT_TIMESTAMP;
COMMIT;

BEGIN;
SET LOCAL app.tenant_id = '{TENANT_B}';
INSERT INTO user_identity (id, issuer, subject, display_name, status)
VALUES ({values["tenant_b_user_id"]}::uuid, {values["issuer"]}, {values["tenant_b"]},
        'Synthetic Smoke Tenant B Dispatcher', 'ACTIVE')
ON CONFLICT (issuer, subject) DO UPDATE
SET display_name = EXCLUDED.display_name, status = 'ACTIVE', updated_at = CURRENT_TIMESTAMP;
INSERT INTO tenant_membership (id, tenant_id, user_identity_id, role, status)
SELECT '{uuid.uuid4()}'::uuid, '{TENANT_B}'::uuid, identity.id, 'DISPATCHER', 'ACTIVE'
FROM user_identity identity
WHERE identity.issuer = {values["issuer"]} AND identity.subject = {values["tenant_b"]}
ON CONFLICT (tenant_id, user_identity_id, role) DO UPDATE
SET status = 'ACTIVE', updated_at = CURRENT_TIMESTAMP;
INSERT INTO project_scope (id, tenant_id, user_identity_id, project_id, status)
SELECT '{uuid.uuid4()}'::uuid, '{TENANT_B}'::uuid, identity.id, '{PROJECT_B}'::uuid, 'ACTIVE'
FROM user_identity identity
WHERE identity.issuer = {values["issuer"]} AND identity.subject = {values["tenant_b"]}
ON CONFLICT (tenant_id, user_identity_id, project_id) DO UPDATE
SET status = 'ACTIVE', updated_at = CURRENT_TIMESTAMP;
COMMIT;
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate ignored synthetic JWT and database smoke fixtures"
    )
    parser.add_argument("--output", type=Path, default=Path(".smoke"))
    parser.add_argument("--issuer", default="http://smoke-issuer.local")
    parser.add_argument("--audience", default="work-order-service")
    parser.add_argument("--lifetime-seconds", type=int, default=900)
    args = parser.parse_args()
    output = args.output
    public_env = (output / "jwt-public.pem").as_posix()
    paths = generate_fixtures(
        output,
        issuer=args.issuer,
        audience=args.audience,
        lifetime_seconds=args.lifetime_seconds,
        public_key_env_path=public_env,
        runtime_db_password=os.getenv("WORK_ORDER_DB_PASSWORD", "work_order_app_dev"),
    )
    print(f"generated smoke env: {paths.environment}")
    print(f"generated provisioning SQL: {paths.provision_sql}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
