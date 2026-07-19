from __future__ import annotations

from uuid import UUID

import httpx
import pytest

from app.quality.callback import QualityCallbackClient, QualityCallbackWorker
from app.quality.models import QualityFindingRecord, QualityResultRecord

TENANT = UUID("11111111-1111-1111-1111-111111111111")
WORK_ORDER = UUID("22222222-2222-2222-2222-222222222222")
JOB = UUID("33333333-3333-3333-3333-333333333333")
RESULT = UUID("44444444-4444-4444-4444-444444444444")


def _result() -> QualityResultRecord:
    return QualityResultRecord(
        id=RESULT,
        tenant_id=TENANT,
        quality_job_id=JOB,
        work_order_id=WORK_ORDER,
        work_order_version=7,
        inspection_round=1,
        verdict="FAIL",
        confidence=0.91,
        work_order_snapshot={
            "id": str(WORK_ORDER),
            "tenant_id": str(TENANT),
            "version": 7,
            "status": "COMPLETED",
            "contact_phone": "must-not-cross-callback",
        },
        policy_versions={"completion-policy": 4},
        attachment_summary=(),
        findings=(
            QualityFindingRecord(
                ordinal=0,
                rule_code="REQUIRED_ATTACHMENT",
                severity="HIGH",
                label="FAIL",
                evidence={"present_count": 0},
                recommendation="Attach completion evidence.",
                confidence=1.0,
                source="RULE",
            ),
        ),
    )


class _Repository:
    def __init__(self) -> None:
        self.result = _result()
        self.delivered = False
        self.pending_calls: list[tuple[UUID, int]] = []
        self.mark_calls: list[tuple[UUID, UUID]] = []

    async def pending_callbacks(
        self,
        tenant_id: UUID,
        limit: int,
    ) -> list[QualityResultRecord]:
        self.pending_calls.append((tenant_id, limit))
        return [] if self.delivered else [self.result]

    async def mark_callback_delivered(self, tenant_id: UUID, result_id: UUID) -> bool:
        self.mark_calls.append((tenant_id, result_id))
        self.delivered = True
        return True


@pytest.mark.asyncio
async def test_callback_marks_delivered_only_after_any_2xx_response() -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(202)

    repository = _Repository()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = QualityCallbackClient(
            "http://work-order-service:8080",
            "service-token",
            client=http_client,
        )
        outcome = await QualityCallbackWorker(repository, client).run_once(TENANT, limit=10)

    assert (outcome.selected, outcome.delivered, outcome.pending) == (1, 1, 0)
    assert repository.mark_calls == [(TENANT, RESULT)]
    request = captured[0]
    assert request.url.path == "/internal/quality-results"
    assert request.headers["authorization"] == "Bearer service-token"
    assert request.headers["idempotency-key"] == str(RESULT)
    body = request.read().decode()
    assert "must-not-cross-callback" not in body
    assert "contact_phone" not in body
    assert str(WORK_ORDER) in body
    assert "REQUIRED_ATTACHMENT" in body


@pytest.mark.asyncio
async def test_callback_timeout_is_redelivered_with_same_idempotency_key() -> None:
    attempts = 0
    keys: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        keys.append(request.headers["idempotency-key"])
        if attempts == 1:
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(204)

    repository = _Repository()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = QualityCallbackClient(
            "http://work-order-service:8080",
            "service-token",
            client=http_client,
        )
        worker = QualityCallbackWorker(repository, client)
        first = await worker.run_once(TENANT, limit=1)
        second = await worker.run_once(TENANT, limit=1)

    assert (first.delivered, first.pending) == (0, 1)
    assert (second.delivered, second.pending) == (1, 0)
    assert repository.mark_calls == [(TENANT, RESULT)]
    assert keys == [str(RESULT), str(RESULT)]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [199, 300, 401, 503])
async def test_non_2xx_callback_is_never_marked(status: int) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    repository = _Repository()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = QualityCallbackClient(
            "http://work-order-service:8080",
            "service-token",
            client=http_client,
        )
        outcome = await QualityCallbackWorker(repository, client).run_once(TENANT, limit=1)

    assert (outcome.delivered, outcome.pending) == (0, 1)
    assert repository.mark_calls == []


def test_callback_client_rejects_credentialed_origin_and_blank_token() -> None:
    with pytest.raises(ValueError, match="origin"):
        QualityCallbackClient("https://user:pass@example.test", "token")
    with pytest.raises(ValueError, match="blank"):
        QualityCallbackClient("https://example.test", "  ")
