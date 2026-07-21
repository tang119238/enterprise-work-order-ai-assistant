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
from app.analytics.router import router as analytics_router
from app.quality.callback import QualityCallbackClient, QualityCallbackWorker
from app.quality.event_client import QualityEventClient
from app.quality.processor import QualityProcessor
from app.quality.repository import PostgresQualityRepository
from app.quality.worker import (
    QualityEventIntakeWorker,
    QualityLifecycle,
    QualityWorker,
    TenantWorkerLoop,
)
from app.security.jwt import JwtAuthenticationError, build_tenant_authenticator
from app.tools.work_order_client import WorkOrderClient

REQUEST_TIMEOUT_SECONDS = 60.0
TenantResolver = Callable[[Request], UUID]


@dataclass(frozen=True)
class _ProductionRuntime:
    dependencies: AgentDependencies
    retrieval_lifecycle: RetrievalLifecycle
    quality_lifecycle: QualityLifecycle
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
    quality_lifecycle: QualityLifecycle | None = None,
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
    fallback_quality_lifecycle = quality_lifecycle or QualityLifecycle()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        nonlocal runtime_dependencies, provider_name
        active_lifecycle = fallback_lifecycle
        active_quality_lifecycle = fallback_quality_lifecycle
        if runtime_dependencies is None:
            runtime = await _build_production_runtime(settings)
            runtime_dependencies = runtime.dependencies
            provider_name = runtime.provider_name
            active_lifecycle = runtime.retrieval_lifecycle
            active_quality_lifecycle = runtime.quality_lifecycle
        application.state.graph = build_graph(runtime_dependencies)
        application.state.retrieval_lifecycle = active_lifecycle
        application.state.quality_lifecycle = active_quality_lifecycle
        application.state.provider_name = provider_name
    application.state.settings = settings
    application.state.gateway = runtime_dependencies.gateway if runtime_dependencies else None
        retrieval_started = False
        try:
            await active_lifecycle.start()
            retrieval_started = True
            await active_quality_lifecycle.start()
            yield
        finally:
            first_error: BaseException | None = None
            try:
                await active_quality_lifecycle.close()
            except BaseException as error:
                first_error = error
            if retrieval_started:
                try:
                    await active_lifecycle.close()
                except BaseException as error:
                    if first_error is None:
                        first_error = error
            if first_error is not None:
                raise first_error

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
    application.state.quality_lifecycle = fallback_quality_lifecycle
    application.state.provider_name = provider_name
    application.state.settings = settings
    application.state.gateway = runtime_dependencies.gateway if runtime_dependencies else None
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

    
    application.include_router(analytics_router)

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
    quality_event_clients: list[QualityEventClient] = []
    quality_callback_clients: list[QualityCallbackClient] = []
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
        gateway = LLMGateway(
            provider=provider,
            fallback_provider=OfflineTemplateProvider(),
            max_retries=settings.llm_max_retries,
            fallback_enabled=settings.llm_fallback_enabled,
        )
        dependencies = AgentDependencies(
            index=index,
            work_order_client=work_order_client,
            gateway=gateway,
        )
        quality_lifecycle = QualityLifecycle()
        quality_tenant_ids = settings.quality_worker_tenant_ids
        if quality_tenant_ids:
            quality_tokens = settings.quality_service_token_values()
            legacy_token = settings.quality_service_token_value()
            if legacy_token and len(quality_tenant_ids) == 1:
                quality_tokens.setdefault(quality_tenant_ids[0], legacy_token)
            missing_tokens = set(quality_tenant_ids) - set(quality_tokens)
            if missing_tokens:
                raise ValueError("each enabled quality worker tenant requires a service token")
            quality_repository = PostgresQualityRepository(database)
            quality_processor = QualityProcessor(
                repository=quality_repository,
                policy_index=index,
                gateway=gateway,
            )
            processor_loop = TenantWorkerLoop(
                QualityWorker(quality_repository, quality_processor),
                tenant_ids=quality_tenant_ids,
                task_name="quality-processor-loop",
                poll_interval_seconds=settings.quality_worker_poll_seconds,
            )
            quality_loops = [processor_loop]
            for tenant_id in quality_tenant_ids:
                event_client = QualityEventClient(
                    settings.work_order_base_url,
                    quality_tokens[tenant_id],
                )
                callback_client = QualityCallbackClient(
                    settings.work_order_base_url,
                    quality_tokens[tenant_id],
                )
                quality_event_clients.append(event_client)
                quality_callback_clients.append(callback_client)
                quality_loops.append(
                    TenantWorkerLoop(
                        QualityEventIntakeWorker(event_client, quality_repository),
                        tenant_ids=(tenant_id,),
                        task_name=f"quality-event-intake-{tenant_id}",
                        poll_interval_seconds=settings.quality_worker_poll_seconds,
                    )
                )
                quality_loops.append(
                    TenantWorkerLoop(
                        QualityCallbackWorker(quality_repository, callback_client),
                        tenant_ids=(tenant_id,),
                        task_name=f"quality-callback-{tenant_id}",
                        poll_interval_seconds=settings.quality_callback_poll_seconds,
                    )
                )
            quality_lifecycle = QualityLifecycle(
                worker_loops=quality_loops,
                shutdown_callbacks=(
                    *(client.close for client in quality_event_clients),
                    *(client.close for client in quality_callback_clients),
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
            quality_lifecycle=quality_lifecycle,
            provider_name=getattr(provider, "provider_name", settings.llm_provider),
        )
    except BaseException:
        await _close_build_resources(
            provider,
            embedding_provider,
            work_order_client,
            *quality_event_clients,
            *quality_callback_clients,
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

