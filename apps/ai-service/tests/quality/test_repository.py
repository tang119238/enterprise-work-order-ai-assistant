from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import httpx
import pytest

from app.quality.event_client import QualityEventClient, QualityEventClientError
from app.quality.models import (
    ClaimedQualityEvent,
    ModelCallAuditRecord,
    QualityFindingRecord,
    QualityJob,
    QualityResultRecord,
)
from app.quality.repository import PostgresQualityRepository

TENANT = UUID("11111111-1111-1111-1111-111111111111")
EVENT_ID = UUID("00000000-0000-0000-0000-000000009401")
WORK_ORDER_ID = UUID("00000000-0000-0000-0000-000000000001")
JOB_ID = UUID("00000000-0000-0000-0000-000000009402")


def _event_payload() -> dict[str, Any]:
    return {
        "event_id": str(EVENT_ID),
        "tenant_id": str(TENANT),
        "work_order_id": str(WORK_ORDER_ID),
        "work_order_version": 7,
        "work_order_snapshot": {
            "id": str(WORK_ORDER_ID),
            "tenant_id": str(TENANT),
            "status": "COMPLETED",
            "version": 7,
        },
        "attachments_summary": [],
        "inspection_round": 1,
        "attempt": 1,
        "occurred_at": "2026-07-20T01:00:00",
    }


class _Mappings:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row

    def first(self) -> dict[str, object] | None:
        return self._row


class _Result:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self._row = row

    def mappings(self) -> _Mappings:
        return _Mappings(self._row)

    def first(self) -> object | None:
        return self._row


class _Session:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(self, statement: object, parameters: dict[str, object]) -> _Result:
        sql = str(statement)
        self.calls.append((sql, parameters))
        if "SELECT id, tenant_id" in sql:
            return _Result(
                {
                    "id": JOB_ID,
                    "tenant_id": TENANT,
                    "work_order_id": WORK_ORDER_ID,
                    "work_order_version": 7,
                    "inspection_round": 1,
                    "business_key": f"{TENANT}:{WORK_ORDER_ID}:7:1",
                    "status": "PENDING",
                }
            )
        return _Result()


class _Database:
    def __init__(self, events: list[str] | None = None) -> None:
        self.session_instance = _Session()
        self.tenant_ids: list[UUID] = []
        self.events = events if events is not None else []

    @asynccontextmanager
    async def session(self, tenant_id: UUID):  # type: ignore[no-untyped-def]
        self.tenant_ids.append(tenant_id)
        self.events.append("transaction_begin")
        yield self.session_instance
        self.events.append("transaction_commit")


@pytest.mark.asyncio
async def test_repeated_completion_event_creates_and_reloads_one_tenant_job() -> None:
    database = _Database()
    repository = PostgresQualityRepository(database)  # type: ignore[arg-type]
    event = ClaimedQualityEvent.model_validate(_event_payload())

    first = await repository.create_from_event(event)
    second = await repository.create_from_event(event)

    assert first == second
    assert first.id == JOB_ID
    assert database.tenant_ids == [TENANT, TENANT]
    insert_sql, parameters = database.session_instance.calls[0]
    normalized_insert_sql = " ".join(insert_sql.split())
    assert (
        "ON CONFLICT (tenant_id, work_order_id, work_order_version, inspection_round) "
        "DO NOTHING" in normalized_insert_sql
    )
    assert parameters["tenant_id"] == TENANT
    assert parameters["work_order_id"] == WORK_ORDER_ID
    assert parameters["business_key"] == f"{TENANT}:{WORK_ORDER_ID}:7:1"
    assert "attachment" in str(parameters["trigger_payload"])
    assert "url" not in str(parameters["trigger_payload"]).lower()


class _Repository:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.calls = 0

    async def create_from_event(self, event: ClaimedQualityEvent) -> QualityJob:
        self.calls += 1
        self._events.append("job_committed")
        return QualityJob(
            id=JOB_ID,
            tenant_id=event.tenant_id,
            work_order_id=event.work_order_id,
            work_order_version=event.work_order_version,
            inspection_round=event.inspection_round,
            business_key=f"{event.tenant_id}:{event.work_order_id}:7:1",
            status="PENDING",
        )


@pytest.mark.asyncio
async def test_consumer_acks_only_after_job_transaction_commits() -> None:
    events: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer service-token"
        if request.url.path.endswith("/claim"):
            assert request.method == "POST"
            assert request.read() == b'{"limit":2}'
            return httpx.Response(200, json=[_event_payload()])
        events.append("ack")
        assert request.url.path == f"/internal/quality-events/{EVENT_ID}/ack"
        return httpx.Response(204)

    repository = _Repository(events)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = QualityEventClient(
            "http://work-order-service:8080",
            "service-token",
            client=http_client,
        )
        jobs = await client.consume_once(repository, limit=2)  # type: ignore[arg-type]

    assert [job.id for job in jobs] == [JOB_ID]
    assert events == ["job_committed", "ack"]


@pytest.mark.asyncio
async def test_ack_network_failure_leaves_job_for_safe_redelivery() -> None:
    events: list[str] = []
    ack_attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal ack_attempts
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json=[_event_payload()])
        ack_attempts += 1
        if ack_attempts == 1:
            raise httpx.ConnectError("synthetic ack failure", request=request)
        events.append("ack")
        return httpx.Response(204)

    repository = _Repository(events)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = QualityEventClient(
            "http://work-order-service:8080",
            "service-token",
            client=http_client,
        )
        with pytest.raises(QualityEventClientError, match="acknowledge"):
            await client.consume_once(repository, limit=1)  # type: ignore[arg-type]
        jobs = await client.consume_once(repository, limit=1)  # type: ignore[arg-type]

    assert repository.calls == 2
    assert jobs[0].id == JOB_ID
    assert events == ["job_committed", "job_committed", "ack"]


@pytest.mark.asyncio
async def test_consumer_does_not_treat_redirect_as_acknowledgement() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json=[_event_payload()])
        return httpx.Response(307, headers={"Location": "https://wrong.invalid/ack"})

    repository = _Repository([])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = QualityEventClient(
            "http://work-order-service:8080",
            "service-token",
            client=http_client,
        )
        with pytest.raises(QualityEventClientError, match="acknowledge"):
            await client.consume_once(repository, limit=1)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_repository_failure_never_acknowledges_event() -> None:
    acked = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal acked
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json=[_event_payload()])
        acked = True
        return httpx.Response(204)

    class FailingRepository:
        async def create_from_event(self, event: ClaimedQualityEvent) -> QualityJob:
            raise RuntimeError("synthetic transaction failure")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = QualityEventClient(
            "http://work-order-service:8080",
            "service-token",
            client=http_client,
        )
        with pytest.raises(RuntimeError, match="transaction failure"):
            await client.consume_once(FailingRepository(), limit=1)

    assert acked is False


@pytest.mark.parametrize("forbidden", ["attachment_url", "database_url", "password", "token"])
def test_claim_model_rejects_sensitive_or_fetchable_payload_fields(forbidden: str) -> None:
    payload = _event_payload()
    payload["work_order_snapshot"][forbidden] = "must-not-cross-boundary"

    with pytest.raises(ValueError, match="forbidden sensitive field"):
        ClaimedQualityEvent.model_validate(payload)


class _PersistenceSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def execute(self, statement: object, parameters: object) -> _Result:
        sql = " ".join(str(statement).split())
        self.calls.append((sql, parameters))
        if "SELECT id FROM quality_job" in sql:
            return _Result({"id": JOB_ID})
        return _Result()


class _PersistenceDatabase:
    def __init__(self) -> None:
        self.session_instance = _PersistenceSession()
        self.events: list[str] = []

    @asynccontextmanager
    async def session(self, tenant_id: UUID):  # type: ignore[no-untyped-def]
        assert tenant_id == TENANT
        self.events.append("begin")
        yield self.session_instance
        self.events.append("commit")


def _quality_result() -> QualityResultRecord:
    return QualityResultRecord(
        id=UUID("00000000-0000-0000-0000-000000009403"),
        tenant_id=TENANT,
        quality_job_id=JOB_ID,
        work_order_id=WORK_ORDER_ID,
        work_order_version=7,
        inspection_round=1,
        verdict="PASS",
        confidence=0.9,
        work_order_snapshot={"status": "COMPLETED"},
        policy_versions={"synthetic-policy": 2},
        attachment_summary=(),
        findings=(
            QualityFindingRecord(
                ordinal=0,
                rule_code="SYNTHETIC_RULE",
                severity="LOW",
                label="PASS",
                evidence={"verified": True},
                recommendation="Keep the evidence.",
                confidence=1.0,
                source="RULE",
            ),
        ),
        model_call=ModelCallAuditRecord(
            id=UUID("00000000-0000-0000-0000-000000009404"),
            provider="synthetic-provider",
            model_name="synthetic-model",
            prompt_version="quality-inspection-v1",
            request_id="synthetic-request",
            latency_ms=10,
            input_summary={"request_hash": "a" * 64},
            response_summary={"response_hash": "b" * 64},
        ),
    )


@pytest.mark.asyncio
async def test_result_bundle_locks_job_and_writes_all_rows_in_one_transaction() -> None:
    database = _PersistenceDatabase()
    repository = PostgresQualityRepository(database)  # type: ignore[arg-type]
    expected = _quality_result()

    actual = await repository.save_result(expected)

    assert actual is expected
    assert database.events == ["begin", "commit"]
    statements = [sql for sql, _ in database.session_instance.calls]
    assert "FOR UPDATE" in statements[0]
    assert "jsonb_build_object" in statements[1]
    assert "INSERT INTO model_call_audit" in statements[2]
    assert "INSERT INTO quality_result" in statements[3]
    assert "INSERT INTO quality_finding" in statements[4]
    assert "UPDATE quality_job" in statements[5]
    assert database.session_instance.calls[4][1][0]["ordinal"] == 0  # type: ignore[index]
