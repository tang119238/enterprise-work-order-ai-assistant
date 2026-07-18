from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
PROJECT_A = "00000000-0000-0000-0000-000000010001"
PROJECT_B = "00000000-0000-0000-0000-000000020001"
TENANT_A_READ_ORDER = "WO-20260718-001"
TENANT_B_READ_ORDER = "WO-20260718-026"
_SAFE_ORDER = re.compile(r"^[A-Z0-9-]{1,64}$")


@dataclass(frozen=True)
class JsonHttpResponse:
    status: int
    body: dict[str, object]
    raw: bytes


@dataclass(frozen=True)
class SmokeConfig:
    ai_base_url: str
    work_order_base_url: str
    wait_seconds: float
    request_timeout_seconds: float
    dispatcher_token: str
    tenant_b_token: str
    ai_token: str
    jwt_issuer: str
    jwt_audience: str
    tenant_a_id: str = TENANT_A
    tenant_b_id: str = TENANT_B
    tenant_a_project_id: str = PROJECT_A
    tenant_b_project_id: str = PROJECT_B
    compose_file: str = "docker-compose.yml"
    postgres_service: str = "postgres"
    postgres_database: str = "workorders"
    postgres_user: str = "postgres"

    @classmethod
    def from_environment(cls) -> SmokeConfig:
        return cls(
            ai_base_url=os.getenv("AI_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
            work_order_base_url=os.getenv(
                "WORK_ORDER_BASE_URL", "http://127.0.0.1:8080"
            ).rstrip("/"),
            wait_seconds=_positive_float("SMOKE_WAIT_SECONDS", "180"),
            request_timeout_seconds=_positive_float(
                "SMOKE_REQUEST_TIMEOUT_SECONDS", "5"
            ),
            dispatcher_token=_required_env("SMOKE_DISPATCHER_TOKEN"),
            tenant_b_token=_required_env("SMOKE_TENANT_B_TOKEN"),
            ai_token=_required_env("SMOKE_AI_TOKEN"),
            jwt_issuer=_required_env("SMOKE_JWT_ISSUER"),
            jwt_audience=_required_env("SMOKE_JWT_AUDIENCE"),
            tenant_a_id=os.getenv("SMOKE_TENANT_A_ID", TENANT_A),
            tenant_b_id=os.getenv("SMOKE_TENANT_B_ID", TENANT_B),
            tenant_a_project_id=os.getenv("SMOKE_TENANT_A_PROJECT_ID", PROJECT_A),
            tenant_b_project_id=os.getenv("SMOKE_TENANT_B_PROJECT_ID", PROJECT_B),
            compose_file=os.getenv("SMOKE_COMPOSE_FILE", "docker-compose.yml"),
            postgres_service=os.getenv("SMOKE_POSTGRES_SERVICE", "postgres"),
            postgres_database=os.getenv("SMOKE_POSTGRES_DATABASE", "workorders"),
            postgres_user=os.getenv("SMOKE_POSTGRES_USER", "postgres"),
        )


Request = Callable[..., JsonHttpResponse]
CountCommandRows = Callable[[SmokeConfig, str], tuple[int, int, int]]


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: Mapping[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float = 5.0,
) -> JsonHttpResponse:
    data = None
    request_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        request_headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(
        url, data=data, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        raw = error.read()
        status = error.code
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AssertionError(f"Expected JSON object from {url}, got HTTP {status}") from error
    if not isinstance(parsed, dict):
        raise AssertionError(f"Expected JSON object from {url}, got HTTP {status}")
    return JsonHttpResponse(status, parsed, raw)


def wait_until_ready(
    url: str,
    wait_seconds: float,
    request_timeout_seconds: float = 2.0,
    request: Request = request_json,
) -> None:
    deadline = time.monotonic() + wait_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = request(url, timeout_seconds=request_timeout_seconds)
            if response.status == 200 and response.body.get("status") in {"UP", "ok"}:
                return
            last_error = AssertionError(
                f"health endpoint returned HTTP {response.status}: {url}"
            )
        except (OSError, TimeoutError, urllib.error.URLError, AssertionError) as error:
            last_error = error
        time.sleep(1.0)
    raise TimeoutError(f"Service did not become ready: {url}; last error: {last_error}")


def count_command_rows(config: SmokeConfig, work_order_no: str) -> tuple[int, int, int]:
    if not _SAFE_ORDER.fullmatch(work_order_no):
        raise RuntimeError("Unsafe synthetic work-order number")
    tenant_id = str(uuid.UUID(config.tenant_a_id))
    compose_path = Path(config.compose_file).resolve()
    if not compose_path.is_file():
        raise RuntimeError(f"Compose file does not exist: {compose_path}")
    sql = f"""
        SELECT w.version,
               (SELECT count(*) FROM work_order_event e
                 WHERE e.tenant_id = w.tenant_id AND e.work_order_id = w.id),
               (SELECT count(*) FROM outbox_event o
                 WHERE o.tenant_id = w.tenant_id AND o.aggregate_id = w.id)
          FROM work_order w
         WHERE w.tenant_id = '{tenant_id}'::uuid
           AND w.work_order_no = '{work_order_no}';
    """
    command = [
        "docker",
        "compose",
        "-f",
        str(compose_path),
        "exec",
        "-T",
        config.postgres_service,
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        config.postgres_user,
        "-d",
        config.postgres_database,
        "-At",
        "-F",
        "|",
        "-c",
        sql,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=compose_path.parent,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except FileNotFoundError as error:
        raise RuntimeError("Docker Compose is required for command row assertions") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Timed out querying command audit rows") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"Could not query command audit rows: {detail}")
    rows = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(rows) != 1:
        raise AssertionError(
            f"Expected exactly one synthetic work order, found {len(rows)}"
        )
    try:
        version, events, outbox = (int(value) for value in rows[0].split("|"))
    except (ValueError, TypeError) as error:
        raise AssertionError(f"Unexpected database count result: {rows[0]}") from error
    return version, events, outbox


def run_smoke_tests(
    config: SmokeConfig,
    *,
    run_id: str | None = None,
    request: Request = request_json,
    wait: Callable[..., None] = wait_until_ready,
    count_command_rows: CountCommandRows = count_command_rows,
    validate_tokens: bool = True,
) -> None:
    if validate_tokens:
        _validate_smoke_tokens(config)
    wait(
        f"{config.work_order_base_url}/actuator/health",
        config.wait_seconds,
        config.request_timeout_seconds,
        request,
    )
    wait(
        f"{config.ai_base_url}/health",
        config.wait_seconds,
        config.request_timeout_seconds,
        request,
    )

    run = (run_id or uuid.uuid4().hex[:12]).lower()
    if not re.fullmatch(r"[0-9a-f]{12}", run):
        raise RuntimeError("Smoke run ID must be exactly 12 hexadecimal characters")
    suffix = run.upper()
    work_order_no = f"SMOKE-{suffix}"
    dispatcher = _bearer(config.dispatcher_token)
    tenant_b = _bearer(config.tenant_b_token)
    ai = _bearer(config.ai_token)

    _expect(
        request,
        f"{config.work_order_base_url}/api/work-orders/{TENANT_A_READ_ORDER}",
        401,
        config.request_timeout_seconds,
    )
    tenant_b_order = _expect(
        request,
        f"{config.work_order_base_url}/api/work-orders/{TENANT_B_READ_ORDER}",
        200,
        config.request_timeout_seconds,
        headers=tenant_b,
    ).body
    assert tenant_b_order.get("work_order_no") == TENANT_B_READ_ORDER
    hidden = _expect(
        request,
        f"{config.work_order_base_url}/api/work-orders/{TENANT_B_READ_ORDER}",
        404,
        config.request_timeout_seconds,
        headers=dispatcher,
    ).body
    assert hidden.get("code") == "WORK_ORDER_NOT_FOUND"
    tenant_a_order = _expect(
        request,
        f"{config.work_order_base_url}/api/work-orders/{TENANT_A_READ_ORDER}",
        200,
        config.request_timeout_seconds,
        headers=dispatcher,
    ).body
    assert tenant_a_order.get("work_order_no") == TENANT_A_READ_ORDER

    create_payload: dict[str, object] = {
        "action_type": "CREATE",
        "parameters": {
            "work_order_no": work_order_no,
            "title": f"Smoke create {suffix}",
            "description": f"Synthetic phase acceptance run {suffix}",
            "project_id": config.tenant_a_project_id,
            "space_path": f"Synthetic/Site-{suffix[:4]}",
            "order_type": "INSPECTION",
            "priority": "MEDIUM",
            "source": "SMOKE_TEST",
            "due_at": "2099-01-01T00:00:00",
        },
    }
    create_proposal = _expect(
        request,
        f"{config.work_order_base_url}/api/action-proposals",
        201,
        config.request_timeout_seconds,
        method="POST",
        payload=create_payload,
        headers=dispatcher,
    ).body
    _assert_authoritative_create_preview(
        create_proposal, create_payload, config.tenant_a_id
    )
    create_proposal_id = _required_text(create_proposal, "id")
    create_key = f"smoke-create-{run}"
    created = _expect(
        request,
        f"{config.work_order_base_url}/api/action-proposals/{create_proposal_id}/confirm",
        200,
        config.request_timeout_seconds,
        method="POST",
        payload={"decision": "CONFIRM"},
        headers={**dispatcher, "Idempotency-Key": create_key},
    ).body
    assert created.get("work_order_no") == work_order_no
    assert created.get("action_type") == "CREATE"
    assert created.get("version") == 0

    before_update = _expect(
        request,
        f"{config.work_order_base_url}/api/work-orders/{work_order_no}",
        200,
        config.request_timeout_seconds,
        headers=dispatcher,
    ).body
    assert before_update.get("version") == 0
    before_counts = count_command_rows(config, work_order_no)
    assert before_counts == (0, 1, 1), (
        "CREATE must produce version 0, one immutable event, and one outbox row; "
        f"got {before_counts}"
    )

    update_payload: dict[str, object] = {
        "action_type": "UPDATE",
        "target_work_order_no": work_order_no,
        "parameters": {"title": f"Smoke update {suffix}"},
    }
    update_proposal = _expect(
        request,
        f"{config.work_order_base_url}/api/action-proposals",
        201,
        config.request_timeout_seconds,
        method="POST",
        payload=update_payload,
        headers=dispatcher,
    ).body
    assert update_proposal.get("status") == "PENDING_CONFIRMATION"
    assert update_proposal.get("expected_version") == 0
    assert _nested(update_proposal, "before_snapshot", "version") == 0
    assert _nested(update_proposal, "after_snapshot", "version") == 1
    assert _nested(update_proposal, "after_snapshot", "title") == f"Smoke update {suffix}"
    update_proposal_id = _required_text(update_proposal, "id")

    ai_denied = _expect(
        request,
        f"{config.work_order_base_url}/api/action-proposals/{update_proposal_id}/confirm",
        403,
        config.request_timeout_seconds,
        method="POST",
        payload={"decision": "CONFIRM"},
        headers={**ai, "Idempotency-Key": f"smoke-ai-denied-{run}"},
    ).body
    assert ai_denied.get("code") == "ACTION_NOT_PERMITTED"

    update_key = f"smoke-update-{run}"
    confirm_url = (
        f"{config.work_order_base_url}/api/action-proposals/"
        f"{update_proposal_id}/confirm"
    )
    first_confirmation = _expect(
        request,
        confirm_url,
        200,
        config.request_timeout_seconds,
        method="POST",
        payload={"decision": "CONFIRM"},
        headers={**dispatcher, "Idempotency-Key": update_key},
    )
    assert first_confirmation.body.get("version") == 1
    after_update = _expect(
        request,
        f"{config.work_order_base_url}/api/work-orders/{work_order_no}",
        200,
        config.request_timeout_seconds,
        headers=dispatcher,
    ).body
    assert after_update.get("version") == int(before_update["version"]) + 1
    assert after_update.get("title") == f"Smoke update {suffix}"
    first_counts = count_command_rows(config, work_order_no)
    assert first_counts == (1, 2, 2), (
        "UPDATE confirmation must add exactly one version, event, and outbox row; "
        f"got {first_counts}"
    )

    replay = _expect(
        request,
        confirm_url,
        200,
        config.request_timeout_seconds,
        method="POST",
        payload={"decision": "CONFIRM"},
        headers={**dispatcher, "Idempotency-Key": update_key},
    )
    assert replay.body == first_confirmation.body
    assert replay.raw == first_confirmation.raw
    after_replay = _expect(
        request,
        f"{config.work_order_base_url}/api/work-orders/{work_order_no}",
        200,
        config.request_timeout_seconds,
        headers=dispatcher,
    ).body
    assert after_replay.get("version") == 1
    replay_counts = count_command_rows(config, work_order_no)
    assert replay_counts == first_counts, (
        "Idempotent replay changed version/event/outbox counts: "
        f"before={first_counts}, after={replay_counts}"
    )


def _expect(
    request: Request,
    url: str,
    expected_status: int,
    timeout_seconds: float,
    *,
    method: str = "GET",
    payload: Mapping[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JsonHttpResponse:
    result = request(
        url,
        method=method,
        payload=payload,
        headers=headers,
        timeout_seconds=timeout_seconds,
    )
    if result.status != expected_status:
        code = result.body.get("code")
        raise AssertionError(
            f"Expected HTTP {expected_status} from {method} {url}, "
            f"got {result.status} ({code})"
        )
    return result


def _assert_authoritative_create_preview(
    proposal: Mapping[str, object],
    request_payload: Mapping[str, object],
    tenant_id: str,
) -> None:
    assert proposal.get("action_type") == "CREATE"
    assert proposal.get("risk_level") == "MEDIUM"
    assert proposal.get("status") == "PENDING_CONFIRMATION"
    assert proposal.get("before_snapshot") is None
    assert proposal.get("expected_version") == 0
    parameters = request_payload.get("parameters")
    after = proposal.get("after_snapshot")
    assert isinstance(parameters, Mapping)
    assert isinstance(after, Mapping)
    for field, value in parameters.items():
        assert after.get(field) == value
    assert after.get("tenant_id") == tenant_id
    assert after.get("status") == "PENDING_DISPATCH"
    assert after.get("version") == 0
    assert isinstance(after.get("id"), str)


def _validate_smoke_tokens(config: SmokeConfig) -> None:
    if len({config.dispatcher_token, config.tenant_b_token, config.ai_token}) != 3:
        raise RuntimeError("Smoke JWTs must be three distinct tokens")
    dispatcher_claims = _validate_claims(
        config.dispatcher_token,
        config,
        expected_tenant=config.tenant_a_id,
        required_roles={"DISPATCHER"},
        required_project=config.tenant_a_project_id,
        label="SMOKE_DISPATCHER_TOKEN",
    )
    _validate_claims(
        config.tenant_b_token,
        config,
        expected_tenant=config.tenant_b_id,
        required_roles={"DISPATCHER"},
        required_project=config.tenant_b_project_id,
        label="SMOKE_TENANT_B_TOKEN",
    )
    ai_claims = _validate_claims(
        config.ai_token,
        config,
        expected_tenant=config.tenant_a_id,
        required_roles={"AI_SERVICE", "DISPATCHER"},
        required_project=config.tenant_a_project_id,
        label="SMOKE_AI_TOKEN",
    )
    if ai_claims.get("sub") != dispatcher_claims.get("sub"):
        raise RuntimeError(
            "SMOKE_AI_TOKEN must use the dispatcher subject with AI_SERVICE added"
        )


def _validate_claims(
    token: str,
    config: SmokeConfig,
    *,
    expected_tenant: str,
    required_roles: set[str],
    required_project: str,
    label: str,
) -> dict[str, Any]:
    claims = _decode_unverified_claims(token, label)
    if claims.get("iss") != config.jwt_issuer:
        raise RuntimeError(f"{label} has the wrong iss claim")
    audience = claims.get("aud")
    audiences = {audience} if isinstance(audience, str) else set(audience or [])
    if config.jwt_audience not in audiences:
        raise RuntimeError(f"{label} has the wrong aud claim")
    if claims.get("tenant_id") != expected_tenant:
        raise RuntimeError(f"{label} has the wrong tenant_id claim")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise RuntimeError(f"{label} is missing a nonblank sub claim")
    roles = set(claims.get("roles") or [])
    if not required_roles.issubset(roles):
        raise RuntimeError(f"{label} is missing required roles")
    projects = set(claims.get("project_ids") or [])
    if required_project not in projects:
        raise RuntimeError(f"{label} is missing the required project_id")
    scope = claims.get("scope")
    if not isinstance(scope, (str, list)) or not scope:
        raise RuntimeError(f"{label} is missing the scope claim")
    now = int(time.time())
    if not isinstance(claims.get("exp"), int) or int(claims["exp"]) <= now:
        raise RuntimeError(f"{label} is expired or missing exp")
    if isinstance(claims.get("nbf"), int) and int(claims["nbf"]) > now:
        raise RuntimeError(f"{label} is not valid yet")
    return claims


def _decode_unverified_claims(token: str, label: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3 or any(not part for part in parts):
        raise RuntimeError(f"{label} is not a compact JWT")
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} has an invalid claims payload") from error
    if not isinstance(claims, dict):
        raise RuntimeError(f"{label} has an invalid claims payload")
    return claims


def _nested(body: Mapping[str, object], outer: str, inner: str) -> object:
    value = body.get(outer)
    if not isinstance(value, Mapping):
        raise AssertionError(f"Expected object field {outer}")
    return value.get(inner)


def _required_text(body: Mapping[str, object], field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value:
        raise AssertionError(f"Expected nonblank string field {field}")
    return value


def _bearer(token: str) -> dict[str, str]:
    if not token.strip():
        raise RuntimeError("Bearer token cannot be blank")
    return {"Authorization": f"Bearer {token}"}


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable is missing: {name}")
    return value


def _positive_float(name: str, default: str) -> float:
    raw = os.getenv(name, default)
    try:
        value = float(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a number") from error
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test the authenticated local Docker Compose stack"
    )
    parser.add_argument("--ai-base-url")
    parser.add_argument("--work-order-base-url")
    parser.add_argument("--wait-seconds", type=float)
    parser.add_argument("--request-timeout-seconds", type=float)
    parser.add_argument("--compose-file")
    args = parser.parse_args()

    config = SmokeConfig.from_environment()
    overrides = {
        name: value
        for name, value in {
            "ai_base_url": args.ai_base_url,
            "work_order_base_url": args.work_order_base_url,
            "wait_seconds": args.wait_seconds,
            "request_timeout_seconds": args.request_timeout_seconds,
            "compose_file": args.compose_file,
        }.items()
        if value is not None
    }
    config = replace(config, **overrides)
    run_smoke_tests(config)
    print("smoke tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
