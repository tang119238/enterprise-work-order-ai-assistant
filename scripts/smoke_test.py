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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
PROJECT_A = "00000000-0000-0000-0000-000000010001"
PROJECT_B = "00000000-0000-0000-0000-000000020001"
TENANT_A_READ_ORDER = "WO-20260718-001"
TENANT_B_READ_ORDER = "WO-20260718-028"
_SAFE_ORDER = re.compile(r"^[A-Z0-9-]{1,64}$")
MAX_TOKEN_LIFETIME_SECONDS = 900


@dataclass(frozen=True)
class JsonHttpResponse:
    status: int
    body: dict[str, object]
    raw: bytes


@dataclass(frozen=True)
class DatabaseCounts:
    work_orders: int
    version: int | None
    events: int
    outbox: int
    proposals: int


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
    jwt_public_key_path: str | None = None
    tenant_a_id: str = TENANT_A
    tenant_b_id: str = TENANT_B
    tenant_a_project_id: str = PROJECT_A
    tenant_b_project_id: str = PROJECT_B
    compose_file: str = "docker-compose.yml"
    postgres_service: str = "postgres"
    postgres_database: str = "workorders"
    runtime_db_user: str = "work_order_app"
    runtime_db_password: str = "work_order_app_dev"

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
            jwt_public_key_path=_required_env("SMOKE_JWT_PUBLIC_KEY_PATH"),
            tenant_a_id=os.getenv("SMOKE_TENANT_A_ID", TENANT_A),
            tenant_b_id=os.getenv("SMOKE_TENANT_B_ID", TENANT_B),
            tenant_a_project_id=os.getenv("SMOKE_TENANT_A_PROJECT_ID", PROJECT_A),
            tenant_b_project_id=os.getenv("SMOKE_TENANT_B_PROJECT_ID", PROJECT_B),
            compose_file=os.getenv("SMOKE_COMPOSE_FILE", "docker-compose.yml"),
            postgres_service=os.getenv("SMOKE_POSTGRES_SERVICE", "postgres"),
            postgres_database=os.getenv("SMOKE_POSTGRES_DATABASE", "workorders"),
            runtime_db_user=os.getenv("SMOKE_RUNTIME_DB_USER", "work_order_app"),
            runtime_db_password=_required_env("SMOKE_RUNTIME_DB_PASSWORD"),
        )


Request = Callable[..., JsonHttpResponse]
CountCommandRows = Callable[[SmokeConfig, str, str, str], DatabaseCounts]


def load_env_file(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"Smoke env file does not exist: {path}")
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError(f"Invalid smoke env line {line_number}")
        name, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise RuntimeError(f"Invalid smoke env name on line {line_number}")
        os.environ.setdefault(name, value)


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
        raise AssertionError(
            f"Expected JSON object from {url}, got HTTP {status}"
        ) from error
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


def wait_until_knowledge_ready(
    config: SmokeConfig,
    *,
    session_id: str,
    headers: Mapping[str, str],
    request: Request = request_json,
) -> dict[str, object]:
    deadline = time.monotonic() + config.wait_seconds
    last_response: JsonHttpResponse | None = None
    while time.monotonic() < deadline:
        last_response = request(
            f"{config.ai_base_url}/chat",
            method="POST",
            payload={
                "session_id": session_id,
                "message": "返工链路规则是什么？",
            },
            headers=headers,
            timeout_seconds=config.request_timeout_seconds,
        )
        warnings = last_response.body.get("warnings")
        if (
            last_response.status == 200
            and last_response.body.get("retrieval_mode") == "hybrid"
            and "rework-policy" in _document_ids(last_response.body)
            and (
                not isinstance(warnings, list)
                or "HYBRID_RETRIEVAL_DEGRADED" not in warnings
            )
        ):
            return last_response.body
        time.sleep(1.0)
    detail = last_response.body if last_response is not None else None
    raise TimeoutError(f"Hybrid knowledge did not become ready: {detail}")


def count_command_rows(
    config: SmokeConfig,
    work_order_no: str,
    proposal_id: str,
    work_order_id: str,
) -> DatabaseCounts:
    command, _sql, compose_dir, process_env = build_count_command(
        config, work_order_no, proposal_id, work_order_id
    )
    try:
        completed = subprocess.run(
            command,
            cwd=compose_dir,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=process_env,
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            "Docker Compose is required for command row assertions"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Timed out querying command audit rows") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"Could not query command audit rows: {detail}")
    rows = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(rows) != 1:
        raise AssertionError(
            f"Expected exactly one database count row, found {len(rows)}"
        )
    try:
        work_orders, version_text, events, outbox, proposals = rows[0].split("|")
        return DatabaseCounts(
            work_orders=int(work_orders),
            version=int(version_text) if version_text else None,
            events=int(events),
            outbox=int(outbox),
            proposals=int(proposals),
        )
    except (ValueError, TypeError) as error:
        raise AssertionError(f"Unexpected database count result: {rows[0]}") from error


def build_count_command(
    config: SmokeConfig,
    work_order_no: str,
    proposal_id: str,
    work_order_id: str,
) -> tuple[list[str], str, Path, dict[str, str]]:
    if not _SAFE_ORDER.fullmatch(work_order_no):
        raise RuntimeError("Unsafe synthetic work-order number")
    tenant_id = str(uuid.UUID(config.tenant_a_id))
    proposal_uuid = str(uuid.UUID(proposal_id))
    work_order_uuid = str(uuid.UUID(work_order_id))
    compose_path = Path(config.compose_file).resolve()
    if not compose_path.is_file():
        raise RuntimeError(f"Compose file does not exist: {compose_path}")
    sql = f"""
        BEGIN;
        SET LOCAL app.tenant_id = '{tenant_id}';
        SELECT
          (SELECT count(*) FROM work_order w
            WHERE w.tenant_id = '{tenant_id}'::uuid
              AND w.work_order_no = '{work_order_no}'),
          (SELECT max(w.version) FROM work_order w
            WHERE w.tenant_id = '{tenant_id}'::uuid
              AND w.work_order_no = '{work_order_no}'),
          (SELECT count(*) FROM work_order_event e
            WHERE e.tenant_id = '{tenant_id}'::uuid
              AND e.work_order_id = '{work_order_uuid}'::uuid),
          (SELECT count(*) FROM outbox_event o
            WHERE o.tenant_id = '{tenant_id}'::uuid
              AND o.aggregate_id = '{work_order_uuid}'::uuid),
          (SELECT count(*) FROM action_proposal p
            WHERE p.tenant_id = '{tenant_id}'::uuid
              AND p.id = '{proposal_uuid}'::uuid);
        COMMIT;
    """
    command = [
        "docker",
        "compose",
        "-f",
        str(compose_path),
        "exec",
        "-T",
        "-e",
        "PGPASSWORD",
        config.postgres_service,
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        config.runtime_db_user,
        "-d",
        config.postgres_database,
        "-qAt",
        "-F",
        "|",
        "-c",
        sql,
    ]
    process_env = os.environ.copy()
    process_env["PGPASSWORD"] = config.runtime_db_password
    return command, sql, compose_path.parent, process_env


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

    unauthenticated_ai = _expect(
        request,
        f"{config.ai_base_url}/chat",
        401,
        config.request_timeout_seconds,
        method="POST",
        payload={
            "session_id": f"smoke-unauthenticated-{run}",
            "message": "返工链路规则是什么？",
        },
    ).body
    detail = unauthenticated_ai.get("detail")
    assert isinstance(detail, Mapping)
    assert detail.get("code") == "AUTHENTICATED_TENANT_REQUIRED"

    unauthenticated = _expect(
        request,
        f"{config.work_order_base_url}/api/work-orders/{TENANT_A_READ_ORDER}",
        401,
        config.request_timeout_seconds,
    ).body
    assert set(unauthenticated) == {"code", "message", "timestamp"}
    assert unauthenticated["code"] == "AUTHENTICATION_REQUIRED"
    assert unauthenticated["message"] == "Authentication required"
    _parse_timestamp(unauthenticated["timestamp"], "401 timestamp")
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

    knowledge = wait_until_knowledge_ready(
        config,
        session_id=f"smoke-knowledge-{run}",
        headers=dispatcher,
        request=request,
    )
    assert _tool_names(knowledge) == set()
    assert "rework-policy" in _document_ids(knowledge)

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
    proposal_requested_at = datetime.now(UTC).replace(tzinfo=None)
    create_proposal = _expect(
        request,
        f"{config.work_order_base_url}/api/action-proposals",
        201,
        config.request_timeout_seconds,
        method="POST",
        payload=create_payload,
        headers=dispatcher,
    ).body
    preview_work_order_id = _assert_authoritative_create_preview(
        create_proposal, create_payload, config.tenant_a_id
    )
    expires_at = _parse_local_datetime(create_proposal.get("expires_at"), "expires_at")
    assert proposal_requested_at + timedelta(minutes=14, seconds=30) <= expires_at
    assert expires_at <= datetime.now(UTC).replace(tzinfo=None) + timedelta(
        minutes=15, seconds=30
    )
    create_proposal_id = _required_text(create_proposal, "id")
    absent_before_confirmation = _expect(
        request,
        f"{config.work_order_base_url}/api/work-orders/{work_order_no}",
        404,
        config.request_timeout_seconds,
        headers=dispatcher,
    ).body
    assert absent_before_confirmation.get("code") == "WORK_ORDER_NOT_FOUND"
    preview_counts = count_command_rows(
        config, work_order_no, create_proposal_id, preview_work_order_id
    )
    assert preview_counts == DatabaseCounts(0, None, 0, 0, 1), (
        "CREATE preview mutated facts or was not persisted authoritatively: "
        f"{preview_counts}"
    )
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
    before_counts = count_command_rows(
        config, work_order_no, create_proposal_id, preview_work_order_id
    )
    assert before_counts == DatabaseCounts(1, 0, 1, 1, 1), (
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
    assert (
        _nested(update_proposal, "after_snapshot", "title") == f"Smoke update {suffix}"
    )
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
    first_counts = count_command_rows(
        config, work_order_no, update_proposal_id, preview_work_order_id
    )
    assert first_counts == DatabaseCounts(1, 1, 2, 2, 1), (
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
    replay_counts = count_command_rows(
        config, work_order_no, update_proposal_id, preview_work_order_id
    )
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


def _tool_names(response: Mapping[str, object]) -> set[str]:
    calls = response.get("tool_calls")
    if not isinstance(calls, list):
        return set()
    return {
        name
        for call in calls
        if isinstance(call, dict)
        and call.get("status") == "success"
        and isinstance((name := call.get("name")), str)
    }


def _document_ids(response: Mapping[str, object]) -> set[str]:
    citations = response.get("citations")
    if not isinstance(citations, list):
        return set()
    return {
        document_id
        for citation in citations
        if isinstance(citation, dict)
        and isinstance((document_id := citation.get("document_id")), str)
    }


def _parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise AssertionError(f"Expected ISO timestamp field {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AssertionError(f"Expected ISO timestamp field {field}") from error
    if parsed.tzinfo is None:
        raise AssertionError(f"Expected timezone-aware timestamp field {field}")
    return parsed


def _parse_local_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise AssertionError(f"Expected ISO local datetime field {field}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise AssertionError(f"Expected ISO local datetime field {field}") from error
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _assert_authoritative_create_preview(
    proposal: Mapping[str, object],
    request_payload: Mapping[str, object],
    tenant_id: str,
) -> str:
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
    return _required_text(after, "id")


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
    header, claims = _decode_token(token, label)
    if header.get("alg") != "RS256":
        raise RuntimeError(f"{label} header alg must be RS256")
    token_type = header.get("typ")
    if token_type is not None and token_type != "JWT":
        raise RuntimeError(f"{label} header typ must be JWT")
    if config.jwt_public_key_path:
        _verify_signature(token, config.jwt_public_key_path, label)

    issuer = _nonblank_string(claims.get("iss"), label, "iss")
    if issuer != config.jwt_issuer:
        raise RuntimeError(f"{label} has the wrong iss claim")
    audience = claims.get("aud")
    if isinstance(audience, str):
        audiences = {_nonblank_string(audience, label, "aud")}
    elif isinstance(audience, list):
        audiences = set(_nonblank_string_list(audience, label, "aud"))
    else:
        raise RuntimeError(f"{label} has an invalid aud claim")
    if config.jwt_audience not in audiences:
        raise RuntimeError(f"{label} has the wrong aud claim")
    subject = _nonblank_string(claims.get("sub"), label, "sub")
    tenant_id = _uuid_string(claims.get("tenant_id"), label, "tenant_id")
    if tenant_id != expected_tenant:
        raise RuntimeError(f"{label} has the wrong tenant_id claim")
    roles_value = claims.get("roles")
    if not isinstance(roles_value, list):
        raise RuntimeError(f"{label} has an invalid roles claim")
    roles = set(_nonblank_string_list(roles_value, label, "roles"))
    if not required_roles.issubset(roles):
        raise RuntimeError(f"{label} is missing required roles")
    projects_value = claims.get("project_ids")
    if not isinstance(projects_value, list):
        raise RuntimeError(f"{label} has an invalid project_ids claim")
    projects = {
        _uuid_string(project, label, "project_ids") for project in projects_value
    }
    if required_project not in projects:
        raise RuntimeError(f"{label} is missing the required project_id")
    scope = claims.get("scope")
    if isinstance(scope, str):
        _nonblank_string(scope, label, "scope")
    elif isinstance(scope, list):
        _nonblank_string_list(scope, label, "scope")
    else:
        raise RuntimeError(f"{label} has an invalid scope claim")
    now = int(time.time())
    not_before = _integer_date(claims.get("nbf"), label, "nbf")
    expires = _integer_date(claims.get("exp"), label, "exp")
    if not_before > now:
        raise RuntimeError(f"{label} nbf claim is not valid yet")
    if expires <= now:
        raise RuntimeError(f"{label} exp claim is expired")
    if expires <= not_before or expires - not_before > MAX_TOKEN_LIFETIME_SECONDS:
        raise RuntimeError(f"{label} lifetime exceeds the smoke bound")
    claims["sub"] = subject
    return claims


def _decode_token(token: str, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    parts = token.split(".")
    if len(parts) != 3 or any(not part for part in parts):
        raise RuntimeError(f"{label} is not a compact JWT")
    header = _decode_json_segment(parts[0], label, "header")
    claims = _decode_json_segment(parts[1], label, "claims")
    return header, claims


def _decode_json_segment(segment: str, label: str, part: str) -> dict[str, Any]:
    try:
        padded = segment + "=" * (-len(segment) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} has an invalid {part} payload") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} {part} must be a JSON object")
    return value


def _verify_signature(token: str, public_key_path: str, label: str) -> None:
    path = Path(public_key_path).resolve()
    if not path.is_file():
        raise RuntimeError(f"{label} public key file does not exist")
    try:
        public_key = serialization.load_pem_public_key(path.read_bytes())
        if not isinstance(public_key, rsa.RSAPublicKey):
            raise RuntimeError(f"{label} public key must be RSA")
        header, claims, signature = token.split(".")
        padded = signature + "=" * (-len(signature) % 4)
        signature_bytes = base64.b64decode(padded, altchars=b"-_", validate=True)
        public_key.verify(
            signature_bytes,
            f"{header}.{claims}".encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except InvalidSignature as error:
        raise RuntimeError(f"{label} signature verification failed") from error
    except (ValueError, UnicodeEncodeError) as error:
        raise RuntimeError(f"{label} signature is invalid") from error


def _nonblank_string(value: object, label: str, claim: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{label} has an invalid {claim} claim")
    return value


def _nonblank_string_list(value: list[object], label: str, claim: str) -> list[str]:
    if not value:
        raise RuntimeError(f"{label} has an invalid {claim} claim")
    return [_nonblank_string(item, label, claim) for item in value]


def _uuid_string(value: object, label: str, claim: str) -> str:
    text = _nonblank_string(value, label, claim)
    try:
        return str(uuid.UUID(text))
    except ValueError as error:
        raise RuntimeError(f"{label} has an invalid {claim} UUID") from error


def _integer_date(value: object, label: str, claim: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"{label} has an invalid {claim} claim")
    return value


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
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()

    if args.env_file is not None:
        load_env_file(args.env_file)
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
