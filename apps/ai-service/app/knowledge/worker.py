from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from app.knowledge.embedding.base import EmbeddingProvider
from app.knowledge.models import WorkerRunResult

_MAX_BATCH_LIMIT = 20


class Worker(Protocol):
    async def run_once(self, tenant_id: UUID, limit: int) -> WorkerRunResult: ...


class _ObservedEmbeddingProvider:
    def __init__(
        self,
        provider: EmbeddingProvider,
        on_success: Callable[[], None],
    ) -> None:
        self._provider = provider
        self._on_success = on_success

    @property
    def model_key(self) -> str:
        return self._provider.model_key

    @property
    def dimensions(self) -> int:
        return self._provider.dimensions

    @property
    def loaded(self) -> bool:
        return self._provider.loaded

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = await self._provider.embed(texts)
        if texts:
            self._on_success()
        return vectors


class RetrievalCapability:
    """Secret-free runtime view of the configured embedding capability."""

    def __init__(
        self,
        provider: EmbeddingProvider,
        *,
        configured: bool,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._configured = configured
        self._clock = clock or (lambda: datetime.now(UTC))
        self._last_embedding_success_at: datetime | None = None
        self.provider: EmbeddingProvider = _ObservedEmbeddingProvider(
            provider,
            self._record_success,
        )

    def snapshot(self) -> dict[str, bool | str | None]:
        model_loaded = self._configured and self.provider.loaded
        succeeded_at = self._last_embedding_success_at
        return {
            "configured": self._configured,
            "model_loaded": model_loaded,
            "last_embedding_success_at": (
                _isoformat_utc(succeeded_at) if succeeded_at is not None else None
            ),
            "mode": "hybrid" if model_loaded else "bm25",
        }

    def _record_success(self) -> None:
        timestamp = self._clock()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("retrieval health clock must be timezone-aware")
        self._last_embedding_success_at = timestamp.astimezone(UTC)


class EmbeddingWorkerLoop:
    """One bounded, tenant-aware embedding worker with cancellable polling."""

    def __init__(
        self,
        worker: Worker,
        *,
        tenant_ids: Sequence[UUID],
        poll_interval_seconds: float = 5.0,
        batch_limit: int = _MAX_BATCH_LIMIT,
    ) -> None:
        if isinstance(batch_limit, bool) or not 1 <= batch_limit <= _MAX_BATCH_LIMIT:
            raise ValueError("batch_limit must be between 1 and 20")
        if (
            isinstance(poll_interval_seconds, bool)
            or not math.isfinite(poll_interval_seconds)
            or poll_interval_seconds <= 0
        ):
            raise ValueError("poll_interval_seconds must be positive and finite")
        if any(not isinstance(tenant_id, UUID) for tenant_id in tenant_ids):
            raise TypeError("worker tenant ids must be UUIDs")
        if len(set(tenant_ids)) != len(tenant_ids):
            raise ValueError("worker tenant ids must be unique")
        self._worker = worker
        self._tenant_ids = tuple(tenant_ids)
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_limit = batch_limit
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(),
            name="embedding-worker-loop",
        )

    async def close(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            for tenant_id in self._tenant_ids:
                if self._stop.is_set():
                    return
                try:
                    await self._worker.run_once(tenant_id, self._batch_limit)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Repository/provider details must not terminate the lifecycle task.
                    # Health remains degraded until a later successful embed.
                    pass
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                continue


class RetrievalLifecycle:
    def __init__(
        self,
        *,
        capability: RetrievalCapability,
        worker_loop: EmbeddingWorkerLoop | None = None,
        shutdown_callbacks: Sequence[Callable[[], Awaitable[None]]] = (),
    ) -> None:
        self.capability = capability
        self._worker_loop = worker_loop
        self._shutdown_callbacks = tuple(shutdown_callbacks)
        self._started = False
        self._closed = False

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("retrieval lifecycle has been closed")
        if self._started:
            return
        if self._worker_loop is not None:
            await self._worker_loop.start()
        self._started = True

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: BaseException | None = None
        if self._worker_loop is not None:
            try:
                await self._worker_loop.close()
            except BaseException as error:
                first_error = error
        for callback in reversed(self._shutdown_callbacks):
            try:
                await callback()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error


def _isoformat_utc(timestamp: datetime) -> str:
    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
