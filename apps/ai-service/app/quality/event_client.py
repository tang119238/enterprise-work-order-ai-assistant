from __future__ import annotations

from typing import Protocol
from uuid import UUID

import httpx
from pydantic import TypeAdapter, ValidationError

from app.quality.models import ClaimedQualityEvent, QualityJob

_EVENTS_ADAPTER = TypeAdapter(list[ClaimedQualityEvent])


class QualityJobRepository(Protocol):
    async def create_from_event(self, event: ClaimedQualityEvent) -> QualityJob: ...


class QualityEventClientError(RuntimeError):
    pass


class QualityEventClient:
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

    async def claim(self, limit: int) -> list[ClaimedQualityEvent]:
        if isinstance(limit, bool) or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        response = await self._post(
            "/internal/quality-events/claim",
            json={"limit": limit},
            expected_status=200,
        )
        try:
            events = _EVENTS_ADAPTER.validate_python(response.json())
        except (ValueError, ValidationError) as error:
            raise QualityEventClientError("quality claim returned invalid data") from error
        if len(events) > limit:
            raise QualityEventClientError("quality claim exceeded the requested limit")
        return events

    async def acknowledge(self, event_id: UUID) -> None:
        if not isinstance(event_id, UUID):
            raise TypeError("event_id must be a UUID")
        await self._post(
            f"/internal/quality-events/{event_id}/ack",
            expected_status=204,
        )

    async def consume_once(
        self,
        repository: QualityJobRepository,
        *,
        limit: int,
    ) -> list[QualityJob]:
        jobs: list[QualityJob] = []
        for event in await self.claim(limit):
            job = await repository.create_from_event(event)
            await self.acknowledge(event.event_id)
            jobs.append(job)
        return jobs

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _post(
        self,
        path: str,
        *,
        json: dict[str, object] | None = None,
        expected_status: int,
    ) -> httpx.Response:
        try:
            response = await self._client.post(
                f"{self._base_url}{path}",
                json=json,
                headers={"Authorization": f"Bearer {self._service_token}"},
                timeout=self._timeout,
            )
        except (httpx.TimeoutException, httpx.RequestError) as error:
            operation = "acknowledge" if path.endswith("/ack") else "claim"
            raise QualityEventClientError(f"quality event {operation} request failed") from error
        if response.status_code != expected_status:
            operation = "acknowledge" if path.endswith("/ack") else "claim"
            raise QualityEventClientError(f"quality event {operation} request failed")
        return response
