from __future__ import annotations

import base64
import json
import time
from collections.abc import Mapping
from dataclasses import replace

import pytest

from scripts import smoke_test


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
            return response(401, {"code": "UNAUTHORIZED"})
        if url.endswith("/WO-20260718-026") and token == "Bearer tenant-b-token":
            return response(200, {"work_order_no": "WO-20260718-026", "version": 0})
        if url.endswith("/WO-20260718-026") and token == "Bearer dispatcher-token":
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
                        "expires_at": "2099-01-01T00:00:00",
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
            assert state["created"]
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
        count_command_rows=lambda *_args, **_kwargs: (
            state["version"], state["events"], state["outbox"]
        ),
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


def test_environment_config_fails_closed_when_a_required_token_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    required = {
        "SMOKE_DISPATCHER_TOKEN": "dispatcher-token",
        "SMOKE_TENANT_B_TOKEN": "tenant-b-token",
        "SMOKE_AI_TOKEN": "ai-token",
        "SMOKE_JWT_ISSUER": "http://issuer.test",
        "SMOKE_JWT_AUDIENCE": "work-order-service",
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
