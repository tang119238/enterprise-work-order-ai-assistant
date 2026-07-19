from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import httpx

from app.quality.models import QualityResultRecord

_MAX_BATCH_LIMIT = 20
_CALLBACK_SNAPSHOT_FIELDS = ("id", "tenant_id", "version", "status", "completed_at")


class QualityCallbackError(RuntimeError):
    pass


@dataclass(frozen=True)
class QualityCallbackRunResult:
    selected: int
    delivered: int
    pending: int


class QualityCallbackRepository(Protocol):
    async def pending_callbacks(
        self,
        tenant_id: UUID,
        limit: int,
    ) -> list[QualityResultRecord]: ...

    async def mark_callback_delivered(self, tenant_id: UUID, result_id: UUID) -> bool: ...


class QualityCallbackClient:
    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        timeout_seconds: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        endpoint = httpx.URL(base_url)
        if (
            endpoint.scheme not in {"http", "https"}
            or not endpoint.host
            or endpoint.userinfo
            or endpoint.path not in {"", "/"}
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("base_url must be an HTTP(S) origin without credentials")
        token = service_token.strip()
        if not token:
            raise ValueError("service_token must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._base_url = str(endpoint).rstrip("/")
        self._service_token = token
        self._timeout = httpx.Timeout(timeout_seconds)
        self._client = client or httpx.AsyncClient(timeout=self._timeout)
        self._owns_client = client is None

    async def deliver(self, result: QualityResultRecord) -> None:
        if not isinstance(result, QualityResultRecord):
            raise TypeError("result must be a QualityResultRecord")
        try:
            response = await self._client.post(
                f"{self._base_url}/internal/quality-results",
                json=_callback_payload(result),
                headers={
                    "Authorization": f"Bearer {self._service_token}",
                    "Idempotency-Key": str(result.id),
                },
                timeout=self._timeout,
                follow_redirects=False,
            )
        except (httpx.TimeoutException, httpx.RequestError) as error:
            raise QualityCallbackError("quality callback request failed") from error
        if not 200 <= response.status_code < 300:
            raise QualityCallbackError("quality callback request failed")

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class QualityCallbackWorker:
    def __init__(
        self,
        repository: QualityCallbackRepository,
        client: QualityCallbackClient,
    ) -> None:
        self._repository = repository
        self._client = client

    async def run_once(self, tenant_id: UUID, limit: int) -> QualityCallbackRunResult:
        if not isinstance(tenant_id, UUID):
            raise TypeError("tenant_id must be a UUID")
        if isinstance(limit, bool) or not 1 <= limit <= _MAX_BATCH_LIMIT:
            raise ValueError("limit must be between 1 and 20")
        results = await self._repository.pending_callbacks(tenant_id, limit)
        delivered = 0
        for result in results:
            if result.tenant_id != tenant_id:
                raise RuntimeError("repository returned a callback for the wrong tenant")
            try:
                await self._client.deliver(result)
            except QualityCallbackError:
                continue
            if await self._repository.mark_callback_delivered(tenant_id, result.id):
                delivered += 1
        return QualityCallbackRunResult(
            selected=len(results),
            delivered=delivered,
            pending=len(results) - delivered,
        )


def _callback_payload(result: QualityResultRecord) -> dict[str, object]:
    snapshot = {
        key: result.work_order_snapshot[key]
        for key in _CALLBACK_SNAPSHOT_FIELDS
        if key in result.work_order_snapshot
    }
    provenance: dict[str, object] | None = None
    if result.model_call is not None:
        provenance = {
            "provider": result.model_call.provider,
            "model": result.model_call.model_name,
            "prompt_version": result.model_call.prompt_version,
            "request_id": result.model_call.request_id,
            "request_hash": result.model_call.input_summary.get("request_hash"),
            "response_hash": result.model_call.response_summary.get("response_hash"),
        }
    return {
        "result_id": str(result.id),
        "quality_job_id": str(result.quality_job_id),
        "tenant_id": str(result.tenant_id),
        "work_order_id": str(result.work_order_id),
        "work_order_version": result.work_order_version,
        "inspection_round": result.inspection_round,
        "verdict": result.verdict,
        "confidence": result.confidence,
        "work_order_snapshot": snapshot,
        "policy_versions": result.policy_versions,
        "findings": [finding.model_dump(mode="json") for finding in result.findings],
        "provenance": provenance,
    }
