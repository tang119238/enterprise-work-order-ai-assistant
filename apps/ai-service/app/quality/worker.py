from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.llm.errors import ProviderError
from app.quality.models import (
    ClaimedQualityEvent,
    ClaimedQualityJob,
    QualityJob,
    QualityResultRecord,
)
from app.quality.processor import QualityOutputError

_MAX_BATCH_LIMIT = 20
_MAX_MODEL_ATTEMPTS = 3


@dataclass(frozen=True)
class QualityWorkerRunResult:
    claimed: int
    succeeded: int
    retried: int
    failed: int
    recovered: int
    completed_at: datetime


class QualityWorkerRepository(Protocol):
    async def recover_expired(
        self,
        tenant_id: UUID,
        *,
        now: datetime,
        lease_expired_before: datetime,
    ) -> int: ...

    async def claim_quality_jobs(
        self,
        tenant_id: UUID,
        limit: int,
        *,
        now: datetime,
    ) -> list[ClaimedQualityJob]: ...

    async def retry_quality_job(
        self,
        job: ClaimedQualityJob,
        *,
        code: str,
        next_retry_at: datetime,
        now: datetime,
    ) -> bool: ...

    async def fail_quality_job(
        self,
        job: ClaimedQualityJob,
        *,
        code: str,
        now: datetime,
    ) -> bool: ...


class QualityJobProcessor(Protocol):
    async def process(self, job: ClaimedQualityJob) -> QualityResultRecord: ...


class TenantWorker(Protocol):
    async def run_once(self, tenant_id: UUID, limit: int) -> object: ...


class QualityEventSource(Protocol):
    async def claim(self, limit: int) -> list[ClaimedQualityEvent]: ...

    async def acknowledge(self, event_id: UUID) -> None: ...


class QualityEventRepository(Protocol):
    async def create_from_event(self, event: ClaimedQualityEvent) -> QualityJob: ...


@dataclass(frozen=True)
class QualityEventRunResult:
    claimed: int
    acknowledged: int


class QualityEventIntakeWorker:
    def __init__(
        self,
        source: QualityEventSource,
        repository: QualityEventRepository,
    ) -> None:
        self._source = source
        self._repository = repository

    async def run_once(self, tenant_id: UUID, limit: int) -> QualityEventRunResult:
        events = await self._source.claim(limit)
        if any(event.tenant_id != tenant_id for event in events):
            raise RuntimeError("quality event source returned the wrong tenant")
        acknowledged = 0
        for event in events:
            await self._repository.create_from_event(event)
            await self._source.acknowledge(event.event_id)
            acknowledged += 1
        return QualityEventRunResult(claimed=len(events), acknowledged=acknowledged)


class QualityWorker:
    def __init__(
        self,
        repository: QualityWorkerRepository,
        processor: QualityJobProcessor,
        *,
        clock: Callable[[], datetime] | None = None,
        lease_timeout: timedelta = timedelta(minutes=15),
        max_attempts: int = _MAX_MODEL_ATTEMPTS,
    ) -> None:
        if lease_timeout <= timedelta(0):
            raise ValueError("lease_timeout must be positive")
        if isinstance(max_attempts, bool) or max_attempts != _MAX_MODEL_ATTEMPTS:
            raise ValueError("quality model attempts must be exactly 3")
        self._repository = repository
        self._processor = processor
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lease_timeout = lease_timeout
        self._max_attempts = max_attempts

    async def run_once(self, tenant_id: UUID, limit: int) -> QualityWorkerRunResult:
        if not isinstance(tenant_id, UUID):
            raise TypeError("tenant_id must be a UUID")
        if isinstance(limit, bool) or not 1 <= limit <= _MAX_BATCH_LIMIT:
            raise ValueError("limit must be between 1 and 20")
        now = _aware(self._clock())
        recovered = await self._repository.recover_expired(
            tenant_id,
            now=now,
            lease_expired_before=now - self._lease_timeout,
        )
        jobs = await self._repository.claim_quality_jobs(
            tenant_id,
            limit,
            now=now,
        )
        succeeded = 0
        retried = 0
        failed = 0
        for job in jobs:
            if job.tenant_id != tenant_id:
                raise RuntimeError("repository returned a job for the wrong tenant")
            try:
                await self._processor.process(job)
            except ProviderError as error:
                if error.retryable and job.retry_count < self._max_attempts:
                    if await self._repository.retry_quality_job(
                        job,
                        code=error.code,
                        next_retry_at=now + retry_delay(job.retry_count),
                        now=now,
                    ):
                        retried += 1
                elif await self._repository.fail_quality_job(
                    job,
                    code=error.code,
                    now=now,
                ):
                    failed += 1
            except QualityOutputError as error:
                if await self._repository.fail_quality_job(
                    job,
                    code=error.code,
                    now=now,
                ):
                    failed += 1
            except (TypeError, ValueError):
                if await self._repository.fail_quality_job(
                    job,
                    code="QUALITY_INPUT_INVALID",
                    now=now,
                ):
                    failed += 1
            except Exception:
                # Unknown faults deliberately retain RUNNING. A different worker
                # recovers the expired lease; guessing a terminal classification
                # here could lose an inspection permanently.
                raise
            else:
                succeeded += 1
        return QualityWorkerRunResult(
            claimed=len(jobs),
            succeeded=succeeded,
            retried=retried,
            failed=failed,
            recovered=recovered,
            completed_at=now,
        )


def retry_delay(attempt: int) -> timedelta:
    if isinstance(attempt, bool) or attempt < 1:
        raise ValueError("attempt must be a positive integer")
    return timedelta(minutes=min(5 * 2 ** (attempt - 1), 60))


class TenantWorkerLoop:
    """Cancellable bounded polling for one tenant-aware worker."""

    def __init__(
        self,
        worker: TenantWorker,
        *,
        tenant_ids: Sequence[UUID],
        task_name: str,
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
        if not task_name.strip():
            raise ValueError("task_name must be nonblank")
        self._worker = worker
        self._tenant_ids = tuple(tenant_ids)
        self._task_name = task_name
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
        self._task = asyncio.create_task(self._run(), name=self._task_name)

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
                    # A claimed RUNNING job is recovered after its lease expires.
                    # One tenant/provider failure must not terminate the process.
                    pass
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                continue


class QualityLifecycle:
    def __init__(
        self,
        *,
        worker_loops: Sequence[TenantWorkerLoop] = (),
        shutdown_callbacks: Sequence[Callable[[], Awaitable[None]]] = (),
    ) -> None:
        self._worker_loops = tuple(worker_loops)
        self._shutdown_callbacks = tuple(shutdown_callbacks)
        self._started = False
        self._closed = False

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("quality lifecycle has been closed")
        if self._started:
            return
        started: list[TenantWorkerLoop] = []
        try:
            for worker_loop in self._worker_loops:
                await worker_loop.start()
                started.append(worker_loop)
        except BaseException:
            for worker_loop in reversed(started):
                await worker_loop.close()
            raise
        self._started = True

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: BaseException | None = None
        for worker_loop in reversed(self._worker_loops):
            try:
                await worker_loop.close()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        for callback in reversed(self._shutdown_callbacks):
            try:
                await callback()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value
