from fastapi import FastAPI, HTTPException

from app.agent.graph import AgentDependencies, build_graph
from app.api.models import ChatRequest, ChatResponse
from app.config import Settings
from app.knowledge.bm25 import BM25PolicyIndex
from app.knowledge.loader import load_policy_directory
from app.llm.errors import ProviderError
from app.llm.gateway import LLMGateway
from app.llm.offline import OfflineTemplateProvider
from app.llm.registry import build_provider
from app.tools.work_order_client import WorkOrderClient


def create_app(
    *,
    settings: Settings | None = None,
    dependencies: AgentDependencies | None = None,
) -> FastAPI:
    settings = settings or Settings()
    if dependencies is None:
        provider = build_provider(settings)
        dependencies = AgentDependencies(
            index=BM25PolicyIndex(load_policy_directory(settings.knowledge_path)),
            work_order_client=WorkOrderClient(settings.work_order_base_url),
            gateway=LLMGateway(
                provider=provider,
                fallback_provider=OfflineTemplateProvider(),
                max_retries=settings.llm_max_retries,
                fallback_enabled=settings.llm_fallback_enabled,
            ),
        )
        provider_name = getattr(provider, "provider_name", settings.llm_provider)
    else:
        provider_name = "offline"
    graph = build_graph(dependencies)
    application = FastAPI(
        title="Enterprise Work Order AI Assistant",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )

    @application.post("/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        try:
            result = await graph.ainvoke(
                {"session_id": request.session_id, "message": request.message}
            )
        except ProviderError as error:
            raise HTTPException(
                status_code=503,
                detail={"code": error.code, "message": str(error)},
            ) from error
        return ChatResponse.model_validate(result["response"])

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "provider": provider_name}

    return application


app = create_app()
