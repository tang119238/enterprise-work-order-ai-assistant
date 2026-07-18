from __future__ import annotations

import base64
import json
import os
import re
import time
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from scripts import generate_smoke_fixtures, smoke_test

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
PROJECT_A = "00000000-0000-0000-0000-000000010001"
PROJECT_B = "00000000-0000-0000-0000-000000020001"
WORK_ORDER_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CREATE_PROPOSAL_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
UPDATE_PROPOSAL_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def test_command_smoke_sequence_uses_strict_decisions_and_proves_replay_is_stable() -> None:
    state = {"created": False, "version": 0, "events": 0, "outbox": 0}
    calls: list[tuple[str, str, Mapping[str, object] | None, Mapping[str, str]]] = []
    count_calls: list[tuple[str, str]] = []
    stable_update = {
        "proposal_id": UPDATE_PROPOSAL_ID,
        "work_order_id": WORK_ORDER_ID,
        "work_order_no": "SMOKE-A1B2C3D4E5F6",
        "action_type": "UPDATE",
        "status": "PENDING_DISPATCH",
        "version": 1,
    }
    stable_raw = json.dumps(stable_update, separators=(",", ":")).encode()

    def request(
        url: str,
        *,
        method: str = "GET",
        payload: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 5.0,
    ) -> smoke_test.JsonHttpResponse:
        del timeout_seconds
        actual_headers = dict(headers or {})
        calls.append((method, url, payload, actual_headers))
        token = actual_headers.get("Authorization")

        if url.endswith("/WO-20260718-001") and token is None:
            return response(
                401,
                {
                    "code": "AUTHENTICATION_REQUIRED",
                    "message": "Authentication required",
                    "timestamp": "2026-07-18T10:00:00Z",
                },
            )
        if url.endswith("/chat"):
            assert payload == {
                "session_id": "smoke-knowledge-a1b2c3d4e5f6",
                "message": "返工链路规则是什么？",
            }
            return response(
                200,
                {
                    "answer": "Synthetic knowledge answer",
                    "citations": [{"document_id": "rework-policy"}],
                    "tool_calls": [],
                },
            )
        if url.endswith(f"/{smoke_test.TENANT_B_READ_ORDER}") and token == "Bearer tenant-b-token":
            return response(200, {"work_order_no": smoke_test.TENANT_B_READ_ORDER, "version": 0})
        if (
            url.endswith(f"/{smoke_test.TENANT_B_READ_ORDER}")
            and token == "Bearer dispatcher-token"
        ):
            return response(404, {"code": "WORK_ORDER_NOT_FOUND"})
        if url.endswith("/WO-20260718-001") and token == "Bearer dispatcher-token":
            return response(200, {"work_order_no": "WO-20260718-001", "version": 0})
        if url.endswith("/api/action-proposals") and payload is not None:
            if payload["action_type"] == "CREATE":
                params = payload["parameters"]
                assert isinstance(params, Mapping)
                return response(
                    201,
                    {
                        "id": CREATE_PROPOSAL_ID,
                        "action_type": "CREATE",
                        "risk_level": "MEDIUM",
                        "status": "PENDING_CONFIRMATION",
                        "before_snapshot": None,
                        "after_snapshot": {
                            **params,
                            "id": WORK_ORDER_ID,
                            "tenant_id": TENANT_A,
                            "status": "PENDING_DISPATCH",
                            "version": 0,
                        },
                        "expected_version": 0,
                        "expires_at": (
                            datetime.now(UTC) + timedelta(minutes=15)
                        ).replace(tzinfo=None).isoformat(),
                    },
                )
            assert payload == {
                "action_type": "UPDATE",
                "target_work_order_no": "SMOKE-A1B2C3D4E5F6",
                "parameters": {"title": "Smoke update A1B2C3D4E5F6"},
            }
            return response(
                201,
                {
                    "id": UPDATE_PROPOSAL_ID,
                    "action_type": "UPDATE",
                    "risk_level": "LOW",
                    "status": "PENDING_CONFIRMATION",
                    "before_snapshot": {"version": 0},
                        "after_snapshot": {
                            "version": 1,
                            "title": "Smoke update A1B2C3D4E5F6",
                        },
                    "expected_version": 0,
                    "expires_at": "2099-01-01T00:00:00",
                },
            )
        if url.endswith(f"/{CREATE_PROPOSAL_ID}/confirm"):
            assert payload == {"decision": "CONFIRM"}
            state.update(created=True, version=0, events=1, outbox=1)
            return response(
                200,
                {
                    "proposal_id": CREATE_PROPOSAL_ID,
                    "work_order_id": WORK_ORDER_ID,
                    "work_order_no": "SMOKE-A1B2C3D4E5F6",
                    "action_type": "CREATE",
                    "status": "PENDING_DISPATCH",
                    "version": 0,
                },
            )
        if url.endswith(f"/{UPDATE_PROPOSAL_ID}/confirm") and token == "Bearer ai-token":
            assert payload == {"decision": "CONFIRM"}
            return response(403, {"code": "ACTION_NOT_PERMITTED"})
        if url.endswith(f"/{UPDATE_PROPOSAL_ID}/confirm"):
            assert payload == {"decision": "CONFIRM"}
            if state["version"] == 0:
                state.update(version=1, events=2, outbox=2)
            return smoke_test.JsonHttpResponse(200, stable_update, stable_raw)
        if url.endswith("/SMOKE-A1B2C3D4E5F6") and token == "Bearer dispatcher-token":
            if not state["created"]:
                return response(404, {"code": "WORK_ORDER_NOT_FOUND"})
            return response(
                200,
                {
                    "id": WORK_ORDER_ID,
                    "work_order_no": "SMOKE-A1B2C3D4E5F6",
                    "title": (
                        "Smoke update A1B2C3D4E5F6"
                        if state["version"] == 1
                        else "Smoke create A1B2C3D4E5F6"
                    ),
                    "status": "PENDING_DISPATCH",
                    "version": state["version"],
                },
            )
        raise AssertionError(f"Unexpected request: {method} {url} {payload} {actual_headers}")

    def count_rows(
        _config: smoke_test.SmokeConfig,
        work_order_no: str,
        proposal_id: str,
    ) -> smoke_test.DatabaseCounts:
        count_calls.append((work_order_no, proposal_id))
        return smoke_test.DatabaseCounts(
            work_orders=1 if state["created"] else 0,
            version=state["version"] if state["created"] else None,
            events=state["events"],
            outbox=state["outbox"],
            proposals=1,
        )

    config = smoke_test.SmokeConfig(
        ai_base_url="http://ai.test",
        work_order_base_url="http://work-order.test",
        wait_seconds=1.0,
        request_timeout_seconds=2.0,
        dispatcher_token="dispatcher-token",
        tenant_b_token="tenant-b-token",
        ai_token="ai-token",
        jwt_issuer="http://issuer.test",
        jwt_audience="work-order-service",
        tenant_a_id=TENANT_A,
        tenant_b_id=TENANT_B,
        tenant_a_project_id=PROJECT_A,
        tenant_b_project_id=PROJECT_B,
        compose_file="docker-compose.yml",
    )

    smoke_test.run_smoke_tests(
        config,
        run_id="a1b2c3d4e5f6",
        request=request,
        wait=lambda *_args, **_kwargs: None,
        count_command_rows=count_rows,
        validate_tokens=False,
    )

    confirm_calls = [call for call in calls if call[1].endswith("/confirm")]
    assert [call[2] for call in confirm_calls] == [
        {"decision": "CONFIRM"},
        {"decision": "CONFIRM"},
        {"decision": "CONFIRM"},
        {"decision": "CONFIRM"},
    ]
    assert confirm_calls[0][3]["Idempotency-Key"].startswith("smoke-create-")
    assert confirm_calls[1][3]["Idempotency-Key"].startswith("smoke-ai-denied-")
    assert confirm_calls[2][3]["Idempotency-Key"] == confirm_calls[3][3]["Idempotency-Key"]
    assert all(work_order == "SMOKE-A1B2C3D4E5F6" for work_order, _ in count_calls)
    assert count_calls == [
        ("SMOKE-A1B2C3D4E5F6", CREATE_PROPOSAL_ID),
        ("SMOKE-A1B2C3D4E5F6", CREATE_PROPOSAL_ID),
        ("SMOKE-A1B2C3D4E5F6", UPDATE_PROPOSAL_ID),
        ("SMOKE-A1B2C3D4E5F6", UPDATE_PROPOSAL_ID),
    ]


def test_tenant_b_read_fixture_matches_the_actual_seed_migrations() -> None:
    v2 = Path(
        "apps/work-order-service/src/main/resources/db/migration/"
        "V2__seed_synthetic_work_orders.sql"
    ).read_text(encoding="utf-8")
    v5 = Path(
        "apps/work-order-service/src/main/resources/db/migration/"
        "V5__split_synthetic_tenants.sql"
    ).read_text(encoding="utf-8")
    project_array = re.search(
        r"ARRAY\[([^]]+)]\)\[\(\(n - 1\) % 3\) \+ 1] AS project_name",
        v2,
    )
    assert project_array is not None
    project_names = re.findall(r"'([^']+)'", project_array.group(1))
    order_number = int(smoke_test.TENANT_B_READ_ORDER.rsplit("-", 1)[1])
    expected_name = project_names[(order_number - 1) % 3]

    tenant_b_block = v5.split(
        "SELECT set_config('app.tenant_id', '22222222-2222-2222-2222-222222222222'",
        1,
    )[1]
    project_rows = dict(
        (name, project_id)
        for project_id, name in re.findall(
            r"\('([^']+)', '22222222-2222-2222-2222-222222222222', '[^']+', '([^']+)'",
            tenant_b_block,
        )
    )
    assert project_rows[expected_name] == smoke_test.PROJECT_B


def test_database_count_command_uses_runtime_role_rls_and_explicit_filters(
    tmp_path: Path,
) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    config = smoke_test.SmokeConfig(
        ai_base_url="http://ai.test",
        work_order_base_url="http://work-order.test",
        wait_seconds=1,
        request_timeout_seconds=1,
        dispatcher_token="dispatcher",
        tenant_b_token="tenant-b",
        ai_token="ai",
        jwt_issuer="http://issuer.test",
        jwt_audience="work-order-service",
        compose_file=str(compose),
        runtime_db_user="work_order_app",
        runtime_db_password="synthetic-runtime-password",
    )

    command, sql, cwd = smoke_test.build_count_command(
        config,
        "SMOKE-A1B2C3D4E5F6",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )

    assert cwd == tmp_path
    assert command[command.index("-U") + 1] == "work_order_app"
    assert "PGPASSWORD=synthetic-runtime-password" in command
    assert "flyway_owner" not in command
    assert sql.index("BEGIN;") < sql.index("SET LOCAL app.tenant_id") < sql.index("SELECT")
    assert "11111111-1111-1111-1111-111111111111" in sql
    assert "SMOKE-A1B2C3D4E5F6" in sql
    assert "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb" in sql
    assert "COMMIT;" in sql


def test_environment_config_fails_closed_when_a_required_token_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    required = {
        "SMOKE_DISPATCHER_TOKEN": "dispatcher-token",
        "SMOKE_TENANT_B_TOKEN": "tenant-b-token",
        "SMOKE_AI_TOKEN": "ai-token",
        "SMOKE_JWT_ISSUER": "http://issuer.test",
        "SMOKE_JWT_AUDIENCE": "work-order-service",
        "SMOKE_JWT_PUBLIC_KEY_PATH": "synthetic-public.pem",
        "SMOKE_RUNTIME_DB_PASSWORD": "synthetic-runtime-password",
    }
    for name, value in required.items():
        monkeypatch.setenv(name, value)
    config = smoke_test.SmokeConfig.from_environment()
    assert config.dispatcher_token == "dispatcher-token"

    monkeypatch.delenv("SMOKE_AI_TOKEN")
    with pytest.raises(RuntimeError, match="SMOKE_AI_TOKEN"):
        smoke_test.SmokeConfig.from_environment()

    # Guard against a future default silently turning missing credentials into a pass.
    assert replace(config, ai_token="").ai_token == ""


def test_generated_env_file_is_loaded_without_overwriting_explicit_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "smoke.env"
    env_file.write_text(
        "SMOKE_DISPATCHER_TOKEN=generated\nSMOKE_JWT_ISSUER=http://generated\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMOKE_JWT_ISSUER", "http://explicit")
    monkeypatch.delenv("SMOKE_DISPATCHER_TOKEN", raising=False)

    smoke_test.load_env_file(env_file)

    assert os.environ["SMOKE_DISPATCHER_TOKEN"] == "generated"
    assert os.environ["SMOKE_JWT_ISSUER"] == "http://explicit"


def test_ai_multi_role_token_must_use_the_proven_dispatcher_subject() -> None:
    dispatcher = token("synthetic-dispatcher", TENANT_A, ["DISPATCHER"], PROJECT_A)
    tenant_b = token("synthetic-dispatcher-b", TENANT_B, ["DISPATCHER"], PROJECT_B)
    ai = token(
        "different-synthetic-ai",
        TENANT_A,
        ["AI_SERVICE", "DISPATCHER"],
        PROJECT_A,
    )
    config = smoke_test.SmokeConfig(
        ai_base_url="http://ai.test",
        work_order_base_url="http://work-order.test",
        wait_seconds=1,
        request_timeout_seconds=1,
        dispatcher_token=dispatcher,
        tenant_b_token=tenant_b,
        ai_token=ai,
        jwt_issuer="http://issuer.test",
        jwt_audience="work-order-service",
    )

    with pytest.raises(RuntimeError, match="dispatcher subject"):
        smoke_test._validate_smoke_tokens(config)


def test_token_preflight_verifies_rs256_signature_and_strict_claims(tmp_path: Path) -> None:
    paths = generate_smoke_fixtures.generate_fixtures(
        tmp_path,
        now=datetime.now(UTC),
        lifetime_seconds=600,
    )
    environment = dict(
        line.split("=", 1)
        for line in paths.environment.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    config = smoke_test.SmokeConfig(
        ai_base_url="http://ai.test",
        work_order_base_url="http://work-order.test",
        wait_seconds=1,
        request_timeout_seconds=1,
        dispatcher_token=environment["SMOKE_DISPATCHER_TOKEN"],
        tenant_b_token=environment["SMOKE_TENANT_B_TOKEN"],
        ai_token=environment["SMOKE_AI_TOKEN"],
        jwt_issuer=environment["SMOKE_JWT_ISSUER"],
        jwt_audience=environment["SMOKE_JWT_AUDIENCE"],
        jwt_public_key_path=str(paths.public_key),
    )
    smoke_test._validate_smoke_tokens(config)

    header, claims = token_parts(config.dispatcher_token)
    now = int(time.time())
    invalid: list[tuple[str, object, dict[str, object]]] = [
        ("header.*object", ["RS256"], claims),
        ("RS256", {**header, "alg": "HS256"}, claims),
        ("iss", header, {**claims, "iss": 7}),
        ("aud", header, {**claims, "aud": [""]}),
        ("sub", header, {**claims, "sub": " "}),
        ("tenant_id", header, {**claims, "tenant_id": "not-a-uuid"}),
        ("roles", header, {**claims, "roles": "DISPATCHER"}),
        ("roles", header, {**claims, "roles": ["DISPATCHER", ""]}),
        ("project_ids", header, {**claims, "project_ids": ["not-a-uuid"]}),
        ("scope", header, {**claims, "scope": [""]}),
        ("nbf", header, {key: value for key, value in claims.items() if key != "nbf"}),
        ("nbf", header, {**claims, "nbf": True}),
        ("nbf", header, {**claims, "nbf": now + 60}),
        ("exp", header, {key: value for key, value in claims.items() if key != "exp"}),
        ("exp", header, {**claims, "exp": "soon"}),
        ("exp", header, {**claims, "exp": now - 1}),
        ("lifetime", header, {**claims, "nbf": now - 1, "exp": now + 901}),
    ]
    for expected, bad_header, bad_claims in invalid:
        bad_token = sign_token(paths.private_key, bad_header, bad_claims)
        with pytest.raises(RuntimeError, match=expected):
            smoke_test._validate_smoke_tokens(
                replace(config, dispatcher_token=bad_token)
            )

    token_prefix, signature = config.dispatcher_token.rsplit(".", 1)
    tampered = token_prefix + "." + ("A" if signature[0] != "A" else "B") + signature[1:]
    with pytest.raises(RuntimeError, match="signature"):
        smoke_test._validate_smoke_tokens(replace(config, dispatcher_token=tampered))


def response(status: int, body: Mapping[str, object]) -> smoke_test.JsonHttpResponse:
    raw = json.dumps(body, separators=(",", ":")).encode()
    return smoke_test.JsonHttpResponse(status, dict(body), raw)


def token(subject: str, tenant: str, roles: list[str], project: str) -> str:
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": "http://issuer.test",
        "sub": subject,
        "aud": ["work-order-service"],
        "exp": int(time.time()) + 300,
        "nbf": int(time.time()) - 1,
        "tenant_id": tenant,
        "roles": roles,
        "project_ids": [project],
        "scope": "work-order:read work-order:write",
    }

    def encode(value: Mapping[str, object]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{encode(header)}.{encode(claims)}.synthetic-signature"


def token_parts(value: str) -> tuple[dict[str, object], dict[str, object]]:
    header, claims, _signature = value.split(".")
    return json.loads(decode_bytes(header)), json.loads(decode_bytes(claims))


def sign_token(
    private_key_path: Path,
    header: object,
    claims: dict[str, object],
) -> str:
    header_segment = encode_json(header)
    claims_segment = encode_json(claims)
    signing_input = f"{header_segment}.{claims_segment}".encode("ascii")
    private_key = serialization.load_pem_private_key(
        private_key_path.read_bytes(), password=None
    )
    signature = private_key.sign(
        signing_input, padding.PKCS1v15(), hashes.SHA256()
    )
    return f"{header_segment}.{claims_segment}.{encode_bytes(signature)}"


def encode_json(value: object) -> str:
    return encode_bytes(json.dumps(value, separators=(",", ":")).encode())


def encode_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def decode_bytes(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
