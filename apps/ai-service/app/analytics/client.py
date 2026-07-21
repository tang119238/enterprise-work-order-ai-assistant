"""HTTP client for Java analytics internal endpoint.

Calls POST /internal/analytics/execute with service token
and user context headers.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)

INTERNAL_ANALYTICS_URL_PATH = "/internal/analytics/execute"


@dataclass
class ExecuteResult:
    """Result from Java analytics executor."""
    columns: list[str]
    rows: list[list[object]]
    truncated: bool
    execution_ms: int
    row_count: int
    audit_id: str


class AnalyticsClientError(Exception):
    """Error calling Java analytics endpoint."""

    def __init__(self, status_code: int, error_code: str, message: str, audit_id: str | None = None):
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.audit_id = audit_id
        super().__init__(f"{error_code}: {message}")


class AnalyticsClient:
    """Client for Java analytics internal endpoint."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.work_order_service_url
        self._service_token = settings.analytics_service_token
        self._timeout = 30.0

    async def execute_sql(
        self,
        sql: str,
        catalog_version: str,
        tenant_id: str,
        user_id: str,
        project_ids: list[str],
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> ExecuteResult:
        """Execute validated SQL via Java analytics endpoint."""
        url = f"{self._base_url}{INTERNAL_ANALYTICS_URL_PATH}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._service_token}",
            "X-Tenant-Id": tenant_id,
            "X-User-Id": user_id,
            "X-Project-Ids": ",".join(project_ids),
            "X-Request-Id": request_id or str(uuid.uuid4()),
            "X-Trace-Id": trace_id or str(uuid.uuid4()),
            "X-Catalog-Version": catalog_version,
        }
        payload = {"sql": sql, "catalog_version": catalog_version}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(url, json=payload, headers=headers)
            except httpx.TimeoutException:
                raise AnalyticsClientError(
                    status_code=504,
                    error_code="SQL_EXECUTION_TIMEOUT",
                    message="Analytics query timed out",
                )
            except httpx.RequestError as e:
                raise AnalyticsClientError(
                    status_code=503,
                    error_code="ANALYTICS_UNAVAILABLE",
                    message=f"Analytics service unavailable: {e}",
                )

        if resp.status_code == 200:
            data = resp.json()
            return ExecuteResult(
                columns=data.get("columns", []),
                rows=data.get("rows", []),
                truncated=data.get("truncated", False),
                execution_ms=data.get("execution_ms", 0),
                row_count=data.get("row_count", 0),
                audit_id=data.get("audit_id", ""),
            )

        # Parse error response
        try:
            err = resp.json()
            error_code = err.get("error_code", "UNKNOWN")
            message = err.get("message", resp.text)
            audit_id = err.get("audit_id")
        except Exception:
            error_code = "UNKNOWN"
            message = resp.text
            audit_id = None

        raise AnalyticsClientError(
            status_code=resp.status_code,
            error_code=error_code,
            message=message,
            audit_id=audit_id,
        )
