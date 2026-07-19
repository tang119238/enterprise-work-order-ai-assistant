from uuid import UUID

import httpx
import pytest
from fastapi import Request

from app.agent.graph import AgentDependencies
from app.api.models import WorkOrderRecord, WorkOrderSearchPage
from app.knowledge.models import ActiveKnowledgeChunk, RetrievalHit, RetrievalResult
from app.llm.gateway import LLMGateway
from app.llm.offline import OfflineTemplateProvider
from app.main import create_app


class ApiStubIndex:
    async def search(
        self,
        tenant_id: UUID,
        query: str,
        limit: int = 5,
    ) -> RetrievalResult:
        candidate = ActiveKnowledgeChunk(
            chunk_id="api-policy:0:0",
            document_id="api-policy",
            document_key="api-policy",
            title="返工规则",
            section="规则",
            text="返工单必须关联根工单。",
            ordinal=0,
            document_version=1,
            content_hash="a" * 64,
        )
        return RetrievalResult(
            hits=(
                RetrievalHit(
                    **candidate.model_dump(),
                    bm25_rank=1,
                    vector_rank=1,
                    rrf_score=2 / 61,
                ),
            ),
            mode="hybrid",
        )


class ApiStubWorkOrderClient:
    async def get_work_order(self, work_order_no: str) -> WorkOrderRecord:
        raise AssertionError("work-order route not expected")

    async def get_rework_chain(self, work_order_no: str) -> list[WorkOrderRecord]:
        raise AssertionError("work-order route not expected")

    async def search_work_orders(self, filters: dict[str, str]) -> WorkOrderSearchPage:
        raise AssertionError("work-order route not expected")


def app_dependencies() -> AgentDependencies:
    offline = OfflineTemplateProvider()
    return AgentDependencies(
        index=ApiStubIndex(),
        work_order_client=ApiStubWorkOrderClient(),
        gateway=LLMGateway(
            provider=offline,
            fallback_provider=offline,
            max_retries=0,
            fallback_enabled=True,
        ),
    )


TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")


def authenticated_tenant(_: Request) -> UUID:
    return TENANT_ID


def build_test_app() -> object:
    return create_app(
        dependencies=app_dependencies(),
        tenant_resolver=authenticated_tenant,
    )


@pytest.mark.asyncio
async def test_chat_contract() -> None:
    transport = httpx.ASGITransport(app=build_test_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/chat",
            json={"session_id": "demo-001", "message": "返工规则是什么？"},
        )

    assert response.status_code == 200
    body = response.json()
    assert {"answer", "citations", "tool_calls", "latency_ms", "model", "warnings"} <= body.keys()


@pytest.mark.asyncio
async def test_chat_rejects_blank_message() -> None:
    transport = httpx.ASGITransport(app=build_test_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/chat", json={"session_id": "demo-001", "message": "   "})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_rejects_message_above_two_thousand_characters() -> None:
    transport = httpx.ASGITransport(app=build_test_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/chat",
            json={"session_id": "demo-001", "message": "问" * 2001},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_health_reports_provider_without_secret() -> None:
    transport = httpx.ASGITransport(app=build_test_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "provider": "offline"}
    assert "key" not in response.text.lower()


@pytest.mark.asyncio
async def test_chat_fails_closed_without_authenticated_tenant_context() -> None:
    transport = httpx.ASGITransport(app=create_app(dependencies=app_dependencies()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/chat",
            json={"session_id": "demo-001", "message": "返工规则是什么？"},
        )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTHENTICATED_TENANT_REQUIRED"


@pytest.mark.asyncio
async def test_chat_rejects_forged_tenant_in_request_body() -> None:
    transport = httpx.ASGITransport(app=build_test_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/chat",
            json={
                "session_id": "demo-001",
                "message": "返工规则是什么？",
                "tenant_id": "22222222-2222-2222-2222-222222222222",
            },
        )

    assert response.status_code == 422
