import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from app.agent.graph import AgentDependencies, PolicyIndex, build_graph
from app.api.models import ChatRequest, ChatResponse
from app.config import Settings
from app.db import Database
from app.knowledge.bm25 import StaticTenantBM25PolicyIndex, TenantBM25PolicyIndex
from app.knowledge.bootstrap import ingest_policy_directory
from app.knowledge.embedding.registry import (
    DisabledEmbeddingProvider,
    build_embedding_provider,
)
from app.knowledge.hybrid import (
    HybridPolicyIndex,
    PostgresActiveChunkSource,
    PostgresVectorPolicyIndex,
)
from app.knowledge.ingest import EmbeddingWorker, KnowledgeIngestor
from app.knowledge.loader import load_policy_directory
from app.knowledge.repository import PostgresKnowledgeRepository
from app.knowledge.worker import (
    EmbeddingWorkerLoop,
    RetrievalCapability,
    RetrievalLifecycle,
)
from app.llm.errors import ProviderError
from app.llm.gateway import LLMGateway
from app.llm.offline import OfflineTemplateProvider
from app.llm.registry import build_provider
from app.security.jwt import JwtAuthenticationError, build_tenant_authenticator
from app.tools.work_order_client import WorkOrderClient

REQUEST_TIMEOUT_SECONDS = 60.0
TenantResolver = Callable[[Request], UUID]


@dataclass(frozen=True)
class _ProductionRuntime:
    dependencies: AgentDependencies
    retrieval_lifecycle: RetrievalLifecycle
    provider_name: str


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
    retrieval_lifecycle: RetrievalLifecycle | None = None,
) -> FastAPI:
    settings = settings or Settings()
    tenant_authenticator = build_tenant_authenticator(settings)
    resolve_authenticated_tenant = tenant_resolver or authenticated_tenant_from_state
    runtime_dependencies = dependencies
    provider_name = (
        getattr(dependencies.gateway.provider, "provider_name", "offline")
        if dependencies is not None
        else settings.llm_provider
    )
    fallback_lifecycle = retrieval_lifecycle or RetrievalLifecycle(
        capability=RetrievalCapability(
            DisabledEmbeddingProvider(),
            configured=False,
        )
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        nonlocal runtime_dependencies, provider_name
        active_lifecycle = fallback_lifecycle
        if runtime_dependencies is None:
            runtime = await _build_production_runtime(settings)
            runtime_dependencies = runtime.dependencies
            provider_name = runtime.provider_name
            active_lifecycle = runtime.retrieval_lifecycle
        application.state.graph = build_graph(runtime_dependencies)
        application.state.retrieval_lifecycle = active_lifecycle
        application.state.provider_name = provider_name
        try:
            await active_lifecycle.start()
            yield
        finally:
            await active_lifecycle.close()

    application = FastAPI(
        title="Enterprise Work Order AI Assistant",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    if runtime_dependencies is not None:
        application.state.graph = build_graph(runtime_dependencies)
    application.state.retrieval_lifecycle = fallback_lifecycle
    application.state.provider_name = provider_name
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if tenant_resolver is None:

        @application.middleware("http")
        async def authenticate_tenant(
            request: Request,
            call_next: Callable[[Request], Awaitable[Response]],
        ) -> Response:
            if request.url.path == "/chat" and tenant_authenticator is not None:
                with suppress(JwtAuthenticationError):
                    request.state.tenant_id = tenant_authenticator.authenticate(
                        request.headers.get("Authorization")
                    )
            return await call_next(request)

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
                application.state.graph.ainvoke(
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
    async def health() -> dict[str, object]:
        lifecycle: RetrievalLifecycle = application.state.retrieval_lifecycle
        return {
            "status": "ok",
            "provider": application.state.provider_name,
            "retrieval": lifecycle.capability.snapshot(),
        }

    return application


async def _build_production_runtime(settings: Settings) -> _ProductionRuntime:
    embedding_provider: object | None = None
    provider: object | None = None
    database: Database | None = None
    work_order_client: WorkOrderClient | None = None
    try:
        embedding_provider = await build_embedding_provider(settings, probe=False)
        capability = RetrievalCapability(
            embedding_provider,
            configured=settings.embedding_provider != "disabled",
        )
        provider = build_provider(settings)
        database = Database(settings)
        work_order_client = WorkOrderClient(settings.work_order_base_url)
        repository = PostgresKnowledgeRepository(database)
        embedding_worker = EmbeddingWorker(
            repository,
            capability.provider,
            retry_delay=timedelta(minutes=5),
        )
        worker_tenant_ids = (
            settings.knowledge_worker_tenant_ids
            if settings.embedding_provider != "disabled"
            else ()
        )
        worker_loop = EmbeddingWorkerLoop(
            embedding_worker,
            tenant_ids=worker_tenant_ids,
            poll_interval_seconds=settings.knowledge_worker_poll_seconds,
            batch_limit=20,
        )
        source = PostgresActiveChunkSource(database)
        index: PolicyIndex
        if settings.embedding_provider == "disabled":
            index = StaticTenantBM25PolicyIndex(load_policy_directory(settings.knowledge_path))
        else:
            await ingest_policy_directory(
                KnowledgeIngestor(repository, model_key=capability.provider.model_key),
                tenant_ids=worker_tenant_ids,
                directory=settings.knowledge_path,
            )
            index = HybridPolicyIndex(
                bm25=TenantBM25PolicyIndex(source),
                vector=PostgresVectorPolicyIndex(database),
                embedding_provider=capability.provider,
            )
        dependencies = AgentDependencies(
            index=index,
            work_order_client=work_order_client,
            gateway=LLMGateway(
                provider=provider,
                fallback_provider=OfflineTemplateProvider(),
                max_retries=settings.llm_max_retries,
                fallback_enabled=settings.llm_fallback_enabled,
            ),
        )
        lifecycle = RetrievalLifecycle(
            capability=capability,
            worker_loop=worker_loop,
            shutdown_callbacks=(
                database.dispose,
                work_order_client.close,
                lambda: _close_if_supported(embedding_provider),
                lambda: _close_if_supported(provider),
            ),
        )
        return _ProductionRuntime(
            dependencies=dependencies,
            retrieval_lifecycle=lifecycle,
            provider_name=getattr(provider, "provider_name", settings.llm_provider),
        )
    except BaseException:
        await _close_build_resources(
            provider,
            embedding_provider,
            work_order_client,
            database,
        )
        raise


async def _close_if_supported(resource: object) -> None:
    close = getattr(resource, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def _close_build_resources(*resources: object | None) -> None:
    for resource in resources:
        if resource is None:
            continue
        try:
            dispose = getattr(resource, "dispose", None)
            if dispose is not None:
                result = dispose()
                if inspect.isawaitable(result):
                    await result
            else:
                await _close_if_supported(resource)
        except BaseException:
            pass


app = create_app()
