from typing import TypedDict
from uuid import UUID

from app.api.models import ChatResponse, Citation, ToolCallRecord, WorkOrderRecord
from app.knowledge.models import RetrievalHit
from app.llm.contracts import LLMResult


class AgentState(TypedDict, total=False):
    tenant_id: UUID
    session_id: str
    message: str
    request_id: str
    route: str
    started_at: float
    knowledge_hits: list[RetrievalHit]
    retrieval_mode: str
    work_orders: list[WorkOrderRecord]
    tool_calls: list[ToolCallRecord]
    warnings: list[str]
    citations: list[Citation]
    answer: str
    model_result: LLMResult
    response: ChatResponse
