from collections.abc import Mapping

import httpx
from pydantic import BaseModel, ValidationError

from app.api.models import WorkOrderRecord, WorkOrderSearchPage


class WorkOrderToolError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WorkOrderClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def get_work_order(self, work_order_no: str) -> WorkOrderRecord:
        response = await self._get(f"/api/work-orders/{work_order_no}")
        return _validate(WorkOrderRecord, response.json())

    async def search_work_orders(self, filters: Mapping[str, str]) -> WorkOrderSearchPage:
        params = {key: value for key, value in filters.items() if value}
        params.update({"page": "0", "size": "20"})
        response = await self._get("/api/work-orders", params=params)
        return _validate(WorkOrderSearchPage, response.json())

    async def get_rework_chain(self, work_order_no: str) -> list[WorkOrderRecord]:
        response = await self._get(f"/api/work-orders/{work_order_no}/rework-chain")
        try:
            payload = response.json()
            return [WorkOrderRecord.model_validate(item) for item in payload]
        except (ValueError, TypeError, ValidationError) as error:
            raise WorkOrderToolError(
                "WORK_ORDER_BAD_RESPONSE", "Work-order service returned invalid data"
            ) from error

    async def _get(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        try:
            if self._client is not None:
                response = await self._client.get(
                    f"{self.base_url}{path}",
                    params=params,
                    timeout=self.timeout_seconds,
                )
            else:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.get(f"{self.base_url}{path}", params=params)
        except (httpx.TimeoutException, httpx.RequestError) as error:
            raise WorkOrderToolError(
                "WORK_ORDER_SERVICE_UNAVAILABLE", "Work-order service is unavailable"
            ) from error
        if response.status_code == 404:
            raise WorkOrderToolError("WORK_ORDER_NOT_FOUND", "Work order was not found")
        if response.status_code >= 400:
            raise WorkOrderToolError(
                "WORK_ORDER_SERVICE_UNAVAILABLE", "Work-order service request failed"
            )
        return response


def _validate[ModelT: BaseModel](model_type: type[ModelT], payload: object) -> ModelT:
    try:
        return model_type.model_validate(payload)
    except ValidationError as error:
        raise WorkOrderToolError(
            "WORK_ORDER_BAD_RESPONSE", "Work-order service returned invalid data"
        ) from error
