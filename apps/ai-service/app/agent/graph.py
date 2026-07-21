import time
import uuid
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.composer import (
    build_citations,
    format_policy_fallback,
    format_work_order_facts,
)
from app.agent.router import extract_search_filters, extract_work_order_no, route_intent
from app.agent.state import AgentState
from app.api.models import (
    ChatResponse,
    ModelMetadata,
    ToolCallRecord,
    WorkOrderRecord,
    WorkOrderSearchPage,
)
from app.knowledge.models import RetrievalResult
from app.llm.contracts import LLMMessage, LLMRequest, LLMResult
from app.llm.gateway import LLMGateway
from app.tools.work_order_client import WorkOrderToolError


class PolicyIndex(Protocol):
    async def search(
        self,
        tenant_id: UUID,
        query: str,
        limit: int = 5,
    ) -> RetrievalResult: ...


class WorkOrderTools(Protocol):
    async def get_work_order(self, work_order_no: str) -> WorkOrderRecord: ...

    async def get_rework_chain(self, work_order_no: str) -> list[WorkOrderRecord]: ...

    async def search_work_orders(self, filters: dict[str, str]) -> WorkOrderSearchPage: ...


@dataclass(frozen=True)
class AgentDependencies:
    index: PolicyIndex
    work_order_client: WorkOrderTools
    gateway: LLMGateway


SYSTEM_PROMPT = """你是企业工单 AI 助手，负责帮助用户查询工单、解答制度问题。

回答规则：
1. 如果提供了工单事实，用简洁专业的语言概括，不要逐字段罗列
2. 如果提供了制度片段，基于片段解释规则，标注引用来源
3. 如果工单未找到，友好提示用户检查工单号
4. 如果问题与工单/制度无关，礼貌说明你是工单助手，引导用户问相关问题
5. 回答要简洁，控制在 200 字以内"""


def build_graph(
    dependencies: AgentDependencies,
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    async def normalize_input(state: AgentState) -> dict[str, object]:
        tenant_id = state.get("tenant_id")
        if not isinstance(tenant_id, UUID):
            raise TypeError("authenticated tenant_id must be a UUID")
        message = state["message"].strip()
        # 空问题处理
        if not message:
            return {
                "tenant_id": tenant_id,
                "message": "",
                "request_id": str(uuid.uuid4()),
                "started_at": time.perf_counter(),
                "knowledge_hits": [],
                "work_orders": [],
                "tool_calls": [],
                "warnings": ["EMPTY_MESSAGE"],
            }
        return {
            "tenant_id": tenant_id,
            "message": message,
            "request_id": str(uuid.uuid4()),
            "started_at": time.perf_counter(),
            "knowledge_hits": [],
            "work_orders": [],
            "tool_calls": [],
            "warnings": [],
        }

    async def route(state: AgentState) -> dict[str, object]:
        if not state["message"]:
            return {"route": "knowledge"}
        return {"route": route_intent(state["message"])}

    async def retrieve_knowledge(state: AgentState) -> dict[str, object]:
        if not state["message"]:
            return {"knowledge_hits": [], "retrieval_mode": "none", "warnings": state.get("warnings", [])}
        retrieval = await dependencies.index.search(
            state["tenant_id"],
            state["message"],
            limit=5,
        )
        return {
            "knowledge_hits": list(retrieval.hits),
            "retrieval_mode": retrieval.mode,
            "warnings": [*state.get("warnings", []), *retrieval.warnings],
        }

    async def call_work_order_tool(state: AgentState) -> dict[str, object]:
        work_order_no = extract_work_order_no(state["message"])
        tool_calls = list(state.get("tool_calls", []))
        if work_order_no and (state["route"] == "combined" or "返工" in state["message"]):
            name = "get_rework_chain"
            arguments: dict[str, str | int | float | bool | None] = {"work_order_no": work_order_no}
        elif work_order_no:
            name = "get_work_order"
            arguments = {"work_order_no": work_order_no}
        else:
            name = "search_work_orders"
            arguments = {}
        try:
            if name == "get_rework_chain":
                if work_order_no is None:
                    raise RuntimeError("work-order number routing invariant failed")
                records: list[
                    WorkOrderRecord
                ] = await dependencies.work_order_client.get_rework_chain(work_order_no)
            elif name == "get_work_order":
                if work_order_no is None:
                    raise RuntimeError("work-order number routing invariant failed")
                records = [await dependencies.work_order_client.get_work_order(work_order_no)]
            else:
                filters = extract_search_filters(state["message"])
                arguments = dict(filters)
                page = await dependencies.work_order_client.search_work_orders(filters)
                records = page.items
            tool_calls.append(ToolCallRecord(name=name, arguments=arguments, status="success"))
            return {"work_orders": records, "tool_calls": tool_calls}
        except WorkOrderToolError as error:
            tool_calls.append(ToolCallRecord(name=name, arguments=arguments, status="error"))
            # 生成友好的错误提示
            if error.code == "WORK_ORDER_NOT_FOUND":
                friendly_msg = f"未找到工单 {work_order_no}，请检查工单号是否正确（格式：WO-YYYYMMDD-NNN）"
            else:
                friendly_msg = "工单服务暂时不可用，请稍后再试"
            return {
                "work_orders": [],
                "tool_calls": tool_calls,
                "warnings": [*state.get("warnings", []), error.code],
                "friendly_error": friendly_msg,
            }

    async def compose_answer(state: AgentState) -> dict[str, object]:
        # 空问题特殊处理
        if "EMPTY_MESSAGE" in state.get("warnings", []):
            return {
                "answer": "请输入您的问题，我可以帮您：\n- 查询工单状态和详情\n- 解答返工规则、SLA 时限等制度问题",
                "citations": [],
                "model_result": LLMResult(content="", provider="offline", model="helper", latency_ms=0),
            }

        facts = format_work_order_facts(state.get("work_orders", []))
        hits = state.get("knowledge_hits", [])
        policy_fallback = format_policy_fallback(hits)
        friendly_error = state.get("friendly_error", "")

        # 构建上下文
        context_parts = []
        if friendly_error:
            context_parts.append(f"系统提示：{friendly_error}")
        if facts:
            context_parts.append(f"工单事实：\n{facts}")
        if hits:
            policy_text = "\n\n".join(f"[{hit.document_id} | {hit.section}] {hit.text}" for hit in hits)
            context_parts.append(f"制度片段：\n{policy_text}")

        context = "\n\n".join(context_parts) if context_parts else ""

        # 所有回答都经过 AI 润色
        if context:
            model_result = await dependencies.gateway.generate(
                LLMRequest(
                    messages=(
                        LLMMessage(role="system", content=SYSTEM_PROMPT),
                        LLMMessage(role="user", content=f"用户问题：{state['message']}\n\n{context}"),
                    ),
                    fallback_text=friendly_error or policy_fallback or facts,
                )
            )
        else:
            # 无任何上下文，AI 直接回答
            model_result = await dependencies.gateway.generate(
                LLMRequest(
                    messages=(
                        LLMMessage(role="system", content=SYSTEM_PROMPT),
                        LLMMessage(role="user", content=state["message"]),
                    ),
                    fallback_text="我是企业工单 AI 助手，可以帮您查询工单、解答制度问题。请问有什么可以帮您？",
                )
            )

        return {
            "answer": model_result.content,
            "citations": build_citations(hits),
            "model_result": model_result,
        }

    async def validate_grounding(state: AgentState) -> dict[str, object]:
        valid_quotes = {hit.text for hit in state.get("knowledge_hits", [])}
        citations = [
            citation for citation in state.get("citations", []) if citation.quote in valid_quotes
        ]
        return {"citations": citations}

    async def build_response(state: AgentState) -> dict[str, object]:
        model_result = state["model_result"]
        response = ChatResponse(
            answer=state["answer"],
            citations=state.get("citations", []),
            tool_calls=state.get("tool_calls", []),
            latency_ms=max(0, round((time.perf_counter() - state["started_at"]) * 1000)),
            model=ModelMetadata(
                provider=model_result.provider,
                name=model_result.model,
                fallback=model_result.fallback,
                error_code=model_result.error_code,
            ),
            retrieval_mode=state.get("retrieval_mode", "none"),
            warnings=[w for w in state.get("warnings", []) if w != "EMPTY_MESSAGE"],
        )
        return {"response": response}

    graph = StateGraph(AgentState)
    graph.add_node("normalize_input", normalize_input)
    graph.add_node("route_intent", route)
    graph.add_node("retrieve_knowledge", retrieve_knowledge)
    graph.add_node("call_work_order_tool", call_work_order_tool)
    graph.add_node("compose_answer", compose_answer)
    graph.add_node("validate_grounding", validate_grounding)
    graph.add_node("build_response", build_response)
    graph.add_edge(START, "normalize_input")
    graph.add_edge("normalize_input", "route_intent")
    graph.add_conditional_edges(
        "route_intent",
        lambda state: state["route"],
        {
            "knowledge": "retrieve_knowledge",
            "work_order": "call_work_order_tool",
            "combined": "call_work_order_tool",
        },
    )
    graph.add_conditional_edges(
        "call_work_order_tool",
        lambda state: "retrieve" if state["route"] == "combined" else "compose",
        {"retrieve": "retrieve_knowledge", "compose": "compose_answer"},
    )
    graph.add_edge("retrieve_knowledge", "compose_answer")
    graph.add_edge("compose_answer", "validate_grounding")
    graph.add_edge("validate_grounding", "build_response")
    graph.add_edge("build_response", END)
    return graph.compile()
