import httpx
import pytest

from app.tools.work_order_client import WorkOrderClient, WorkOrderToolError


@pytest.mark.asyncio
async def test_get_work_order_maps_public_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://work-order-service:8080/api/work-orders/WO-20260718-001"
        return httpx.Response(
            200,
            json={
                "work_order_no": "WO-20260718-001",
                "title": "照明异常",
                "project_name": "星河中心",
                "status": "PENDING_ACCEPTANCE",
                "priority": "HIGH",
                "assignee_name": "林晓",
                "created_at": "2026-07-18T08:20:00",
                "due_at": "2026-07-18T16:20:00",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = WorkOrderClient("http://work-order-service:8080", client=client)
        result = await tool.get_work_order("WO-20260718-001")

    assert result.work_order_no == "WO-20260718-001"
    assert result.assignee_name == "林晓"


@pytest.mark.asyncio
async def test_search_sends_only_present_filters() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {
            "status": "PROCESSING",
            "page": "0",
            "size": "20",
        }
        return httpx.Response(
            200,
            json={"items": [], "page": 0, "size": 20, "total": 0, "total_pages": 0},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = WorkOrderClient("http://work-order-service:8080", client=client)
        result = await tool.search_work_orders({"status": "PROCESSING"})

    assert result.total == 0
    assert result.items == []


@pytest.mark.asyncio
async def test_missing_order_maps_to_stable_tool_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "WORK_ORDER_NOT_FOUND"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = WorkOrderClient("http://work-order-service:8080", client=client)
        with pytest.raises(WorkOrderToolError) as error:
            await tool.get_work_order("WO-20260718-999")

    assert error.value.code == "WORK_ORDER_NOT_FOUND"
