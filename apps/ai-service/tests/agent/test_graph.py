from uuid import UUID

import pytest

from app.agent.graph import AgentDependencies, build_graph
from app.api.models import WorkOrderRecord, WorkOrderSearchPage
from app.knowledge.hybrid import HYBRID_RETRIEVAL_DEGRADED
from app.knowledge.models import ActiveKnowledgeChunk, RetrievalHit, RetrievalResult
from app.llm.gateway import LLMGateway
from app.llm.offline import OfflineTemplateProvider


class StubIndex:
    def __init__(self, *, degraded: bool = False) -> None:
        self.degraded = degraded
        self.calls: list[tuple[UUID, str, int]] = []

    async def search(
        self,
        tenant_id: UUID,
        query: str,
        limit: int = 5,
    ) -> RetrievalResult:
        self.calls.append((tenant_id, query, limit))
        hit = RetrievalHit(
            **ActiveKnowledgeChunk(
                chunk_id="rework-policy:1:0",
                document_id="rework-policy",
                document_key="rework-policy",
                title="返工处理规则",
                section="3.2 返工链路",
                text="返工单必须关联根工单，并按创建时间展示完整链路。",
                ordinal=0,
                document_version=1,
                content_hash="a" * 64,
            ).model_dump(),
            bm25_rank=1,
            vector_rank=1,
            rrf_score=2 / 61,
        )
        return RetrievalResult(
            hits=(hit,),
            mode="bm25" if self.degraded else "hybrid",
            warnings=(HYBRID_RETRIEVAL_DEGRADED,) if self.degraded else (),
        )


class StubWorkOrderClient:
    async def get_work_order(self, work_order_no: str) -> WorkOrderRecord:
        return sample_order(work_order_no)

    async def get_rework_chain(self, work_order_no: str) -> list[WorkOrderRecord]:
        return [
            sample_order("WO-20260718-007"),
            sample_order(work_order_no, root="WO-20260718-007"),
        ]

    async def search_work_orders(self, filters: dict[str, str]) -> WorkOrderSearchPage:
        return WorkOrderSearchPage(
            items=[sample_order("WO-20260718-003")],
            page=0,
            size=20,
            total=1,
            total_pages=1,
        )


TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")


def dependencies(index: StubIndex | None = None) -> AgentDependencies:
    offline = OfflineTemplateProvider()
    return AgentDependencies(
        index=index or StubIndex(),
        work_order_client=StubWorkOrderClient(),
        gateway=LLMGateway(
            provider=offline,
            fallback_provider=offline,
            max_retries=0,
            fallback_enabled=True,
        ),
    )


@pytest.mark.asyncio
async def test_knowledge_route_returns_real_citation_without_tool_call() -> None:
    result = await build_graph(dependencies()).ainvoke(
        {"tenant_id": TENANT_ID, "session_id": "demo", "message": "返工链路有什么规则？"}
    )
    response = result["response"]

    assert response.citations[0].document_id == "rework-policy"
    assert response.tool_calls == []
    assert response.model.provider == "offline"


@pytest.mark.asyncio
async def test_work_order_route_returns_deterministic_facts_and_tool_audit() -> None:
    result = await build_graph(dependencies()).ainvoke(
        {
            "tenant_id": TENANT_ID,
            "session_id": "demo",
            "message": "查询 WO-20260718-001 当前状态",
        }
    )
    response = result["response"]

    assert "WO-20260718-001" in response.answer
    assert "PENDING_ACCEPTANCE" in response.answer
    assert response.tool_calls[0].name == "get_work_order"
    assert response.citations == []


@pytest.mark.asyncio
async def test_combined_route_returns_rework_chain_and_policy_citation() -> None:
    result = await build_graph(dependencies()).ainvoke(
        {
            "tenant_id": TENANT_ID,
            "session_id": "demo",
            "message": "WO-20260718-008 为什么是返工单，怎么处理？",
        }
    )
    response = result["response"]

    assert response.tool_calls[0].name == "get_rework_chain"
    assert response.citations[0].document_id == "rework-policy"
    assert "WO-20260718-007" in response.answer
    assert "WO-20260718-008" in response.answer


@pytest.mark.asyncio
async def test_retrieval_uses_authenticated_tenant_and_propagates_degradation() -> None:
    index = StubIndex(degraded=True)
    result = await build_graph(dependencies(index)).ainvoke(
        {"tenant_id": TENANT_ID, "session_id": "demo", "message": "返工规则是什么？"}
    )

    assert index.calls == [(TENANT_ID, "返工规则是什么？", 5)]
    assert result["response"].warnings == [HYBRID_RETRIEVAL_DEGRADED]
    assert result["retrieval_mode"] == "bm25"


@pytest.mark.asyncio
async def test_graph_rejects_missing_authenticated_tenant_context() -> None:
    with pytest.raises((KeyError, TypeError, ValueError), match="tenant"):
        await build_graph(dependencies()).ainvoke(
            {"session_id": "demo", "message": "返工规则是什么？"}
        )


def sample_order(work_order_no: str, root: str | None = None) -> WorkOrderRecord:
    return WorkOrderRecord(
        work_order_no=work_order_no,
        title="照明异常",
        project_name="星河中心",
        status="PENDING_ACCEPTANCE",
        priority="HIGH",
        assignee_name="林晓",
        root_work_order_no=root,
        created_at="2026-07-18T08:20:00",
        due_at="2026-07-18T16:20:00",
    )
