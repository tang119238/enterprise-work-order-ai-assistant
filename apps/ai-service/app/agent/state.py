from typing import TypedDict

from app.api.models import ChatResponse, Citation, ToolCallRecord, WorkOrderRecord
from app.knowledge.models import SearchHit
from app.llm.contracts import LLMResult


class AgentState(TypedDict, total=False):
    session_id: str
    message: str
    request_id: str
    route: str
    started_at: float
    knowledge_hits: list[SearchHit]
    work_orders: list[WorkOrderRecord]
    tool_calls: list[ToolCallRecord]
    warnings: list[str]
    citations: list[Citation]
    answer: str
    model_result: LLMResult
    response: ChatResponse
