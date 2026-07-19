import asyncio
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from app.agent.graph import AgentDependencies, build_graph
from app.api.models import ChatRequest, ChatResponse
from app.config import Settings
from app.knowledge.bm25 import StaticTenantBM25PolicyIndex
from app.knowledge.loader import load_policy_directory
from app.llm.errors import ProviderError
from app.llm.gateway import LLMGateway
from app.llm.offline import OfflineTemplateProvider
from app.llm.registry import build_provider
from app.tools.work_order_client import WorkOrderClient

REQUEST_TIMEOUT_SECONDS = 60.0
TenantResolver = Callable[[Request], UUID]


def authenticated_tenant_from_state(request: Request) -> UUID:
    tenant_id = getattr(request.state, "tenant_id", None)
    if not isinstance(tenant_id, UUID):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "AUTHENTICATED_TENANT_REQUIRED",
                "message": "Authenticated tenant context is required",
            },
        )
    return tenant_id


def create_app(
    *,
    settings: Settings | None = None,
    dependencies: AgentDependencies | None = None,
    tenant_resolver: TenantResolver | None = None,
) -> FastAPI:
    settings = settings or Settings()
    resolve_authenticated_tenant = tenant_resolver or authenticated_tenant_from_state
    if dependencies is None:
        provider = build_provider(settings)
        dependencies = AgentDependencies(
            index=StaticTenantBM25PolicyIndex(
                load_policy_directory(settings.knowledge_path)
            ),
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
        provider_name = getattr(
            dependencies.gateway.provider, "provider_name", "offline"
        )
    graph = build_graph(dependencies)
    application = FastAPI(
        title="Enterprise Work Order AI Assistant",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def tenant_dependency(request: Request) -> UUID:
        tenant_id = resolve_authenticated_tenant(request)
        if not isinstance(tenant_id, UUID):
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "AUTHENTICATED_TENANT_REQUIRED",
                    "message": "Authenticated tenant context is required",
                },
            )
        return tenant_id

    @application.post("/chat", response_model=ChatResponse)
    async def chat(
        request: ChatRequest,
        tenant_id: Annotated[UUID, Depends(tenant_dependency)],
    ) -> ChatResponse:
        try:
            result = await asyncio.wait_for(
                graph.ainvoke(
                    {
                        "tenant_id": tenant_id,
                        "session_id": request.session_id,
                        "message": request.message,
                    }
                ),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            raise HTTPException(
                status_code=504,
                detail={"code": "REQUEST_TIMEOUT", "message": "Request timed out"},
            ) from None
        except ProviderError as error:
            raise HTTPException(
                status_code=503,
                detail={"code": error.code, "message": str(error)},
            ) from error
        except Exception:
            raise HTTPException(
                status_code=500,
                detail={"code": "INTERNAL_ERROR", "message": "Internal server error"},
            ) from None
        return ChatResponse.model_validate(result["response"])

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "provider": provider_name}

    return application


app = create_app()
