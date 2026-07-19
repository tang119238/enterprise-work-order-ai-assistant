import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from fastapi import Request

from app.agent.graph import AgentDependencies
from app.api.models import WorkOrderRecord, WorkOrderSearchPage
from app.config import Settings
from app.knowledge.embedding.base import (
    EMBEDDING_DIMENSIONS,
    EmbeddingProviderUnavailableError,
)
from app.knowledge.models import RetrievalResult, WorkerRunResult
from app.knowledge.worker import (
    EmbeddingWorkerLoop,
    RetrievalCapability,
    RetrievalLifecycle,
)
from app.llm.gateway import LLMGateway
from app.llm.offline import OfflineTemplateProvider
from app.main import create_app


class FakeProvider:
    def __init__(self, *, fail: bool = False) -> None:
        self._loaded = False
        self._fail = fail
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    @property
    def model_key(self) -> str:
        return "synthetic-512"

    @property
    def dimensions(self) -> int:
        return EMBEDDING_DIMENSIONS

    @property
    def loaded(self) -> bool:
        return self._loaded

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.entered.set()
        await self.release.wait()
        if self._fail:
            raise EmbeddingProviderUnavailableError
        self._loaded = True
        return [[1.0, *([0.0] * (EMBEDDING_DIMENSIONS - 1))] for _ in texts]


class FakeWorker:
    def __init__(self, run: Callable[[], Awaitable[WorkerRunResult]]) -> None:
        self._run = run
        self.calls: list[tuple[UUID, int]] = []

    async def run_once(self, tenant_id: UUID, limit: int) -> WorkerRunResult:
        self.calls.append((tenant_id, limit))
        return await self._run()


TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")
NOW = datetime(2026, 7, 20, 3, 30, tzinfo=UTC)


class NeverIndex:
    async def search(
        self,
        tenant_id: UUID,
        query: str,
        limit: int = 5,
    ) -> RetrievalResult:
        raise AssertionError("chat is not expected")


class NeverWorkOrderClient:
    async def get_work_order(self, work_order_no: str) -> WorkOrderRecord:
        raise AssertionError("chat is not expected")

    async def get_rework_chain(self, work_order_no: str) -> list[WorkOrderRecord]:
        raise AssertionError("chat is not expected")

    async def search_work_orders(self, filters: dict[str, str]) -> WorkOrderSearchPage:
        raise AssertionError("chat is not expected")


def app_dependencies() -> AgentDependencies:
    offline = OfflineTemplateProvider()
    return AgentDependencies(
        index=NeverIndex(),
        work_order_client=NeverWorkOrderClient(),
        gateway=LLMGateway(
            provider=offline,
            fallback_provider=offline,
            max_retries=0,
            fallback_enabled=True,
        ),
    )


def authenticated_tenant(_: Request) -> UUID:
    return TENANT_ID


async def get_health(capability: RetrievalCapability) -> dict[str, object]:
    app = create_app(
        dependencies=app_dependencies(),
        tenant_resolver=authenticated_tenant,
        retrieval_lifecycle=RetrievalLifecycle(capability=capability),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    return response.json()["retrieval"]


@pytest.mark.asyncio
async def test_health_distinguishes_configured_but_not_loaded() -> None:
    capability = RetrievalCapability(FakeProvider(), configured=True, clock=lambda: NOW)

    assert await get_health(capability) == {
        "configured": True,
        "model_loaded": False,
        "last_embedding_success_at": None,
        "mode": "bm25",
    }


@pytest.mark.asyncio
async def test_health_reports_disabled_without_exposing_configuration() -> None:
    capability = RetrievalCapability(FakeProvider(), configured=False, clock=lambda: NOW)

    health = await get_health(capability)

    assert health == {
        "configured": False,
        "model_loaded": False,
        "last_embedding_success_at": None,
        "mode": "bm25",
    }
    assert "provider" not in health
    assert "key" not in str(health).lower()


@pytest.mark.asyncio
async def test_successful_embedding_updates_health_timestamp_and_mode() -> None:
    provider = FakeProvider()
    provider.release.set()
    capability = RetrievalCapability(provider, configured=True, clock=lambda: NOW)

    await capability.provider.embed(["成功"])

    assert await get_health(capability) == {
        "configured": True,
        "model_loaded": True,
        "last_embedding_success_at": "2026-07-20T03:30:00Z",
        "mode": "hybrid",
    }


@pytest.mark.asyncio
async def test_first_model_failure_does_not_claim_loaded_or_success() -> None:
    provider = FakeProvider(fail=True)
    provider.release.set()
    capability = RetrievalCapability(provider, configured=True, clock=lambda: NOW)

    with pytest.raises(EmbeddingProviderUnavailableError):
        await capability.provider.embed(["首次下载"])

    assert capability.snapshot() == {
        "configured": True,
        "model_loaded": False,
        "last_embedding_success_at": None,
        "mode": "bm25",
    }


@pytest.mark.asyncio
async def test_worker_is_bounded_and_shutdown_cancels_first_download() -> None:
    provider = FakeProvider()
    capability = RetrievalCapability(provider, configured=True, clock=lambda: NOW)

    async def run() -> WorkerRunResult:
        await capability.provider.embed(["后台预热"])
        return WorkerRunResult(
            claimed=1,
            succeeded=1,
            retried=0,
            failed=0,
            completed_at=NOW,
        )

    worker = FakeWorker(run)
    loop = EmbeddingWorkerLoop(
        worker,
        tenant_ids=(TENANT_ID,),
        poll_interval_seconds=60,
        batch_limit=20,
    )
    closed: list[str] = []

    async def close_resource() -> None:
        closed.append("closed")

    lifecycle = RetrievalLifecycle(
        capability=capability,
        worker_loop=loop,
        shutdown_callbacks=(close_resource,),
    )
    app = create_app(
        dependencies=app_dependencies(),
        tenant_resolver=authenticated_tenant,
        retrieval_lifecycle=lifecycle,
    )

    async with app.router.lifespan_context(app):
        await asyncio.wait_for(provider.entered.wait(), timeout=0.5)
        assert worker.calls == [(TENANT_ID, 20)]

    assert closed == ["closed"]
    assert loop.running is False


def test_worker_rejects_batches_above_twenty() -> None:
    async def never_run() -> WorkerRunResult:
        raise AssertionError("not started")

    with pytest.raises(ValueError, match="between 1 and 20"):
        EmbeddingWorkerLoop(
            FakeWorker(never_run),
            tenant_ids=(TENANT_ID,),
            batch_limit=21,
        )


@pytest.mark.asyncio
async def test_production_lifespan_does_not_probe_or_download_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider()

    async def build_without_probe(
        _: Settings,
        **kwargs: object,
    ) -> FakeProvider:
        assert kwargs == {"probe": False}
        return provider

    monkeypatch.setattr("app.main.build_embedding_provider", build_without_probe)
    app = create_app(
        settings=Settings(
            embedding_provider="local",
            knowledge_worker_tenant_ids=(),
            _env_file=None,
        ),
        tenant_resolver=authenticated_tenant,
    )

    async with app.router.lifespan_context(app):
        assert provider.entered.is_set() is False
        assert app.state.retrieval_lifecycle.capability.snapshot()["configured"] is True


@pytest.mark.asyncio
async def test_shutdown_attempts_every_resource_after_one_close_failure() -> None:
    events: list[str] = []

    async def close_database() -> None:
        events.append("database")

    async def close_http_with_failure() -> None:
        events.append("http")
        raise RuntimeError("synthetic close failure")

    lifecycle = RetrievalLifecycle(
        capability=RetrievalCapability(FakeProvider(), configured=True),
        shutdown_callbacks=(close_database, close_http_with_failure),
    )

    with pytest.raises(RuntimeError, match="synthetic close failure"):
        await lifecycle.close()

    assert events == ["http", "database"]


def test_worker_tenant_ids_parse_from_comma_separated_environment_value() -> None:
    settings = Settings(
        knowledge_worker_tenant_ids=(
            "11111111-1111-1111-1111-111111111111,"
            "22222222-2222-2222-2222-222222222222"
        ),
        _env_file=None,
    )

    assert settings.knowledge_worker_tenant_ids == (
        UUID("11111111-1111-1111-1111-111111111111"),
        UUID("22222222-2222-2222-2222-222222222222"),
    )
