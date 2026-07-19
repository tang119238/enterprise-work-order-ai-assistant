from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.llm.errors import (
    ProviderAuthError,
    ProviderBadResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.quality.models import ClaimedQualityJob, QualityResultRecord
from app.quality.processor import QualityOutputError
from app.quality.worker import QualityLifecycle, QualityWorker, TenantWorkerLoop, retry_delay

TENANT = UUID("11111111-1111-1111-1111-111111111111")
WORK_ORDER = UUID("22222222-2222-2222-2222-222222222222")
JOB = UUID("33333333-3333-3333-3333-333333333333")
NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def _job(attempt: int = 1) -> ClaimedQualityJob:
    return ClaimedQualityJob(
        id=JOB,
        tenant_id=TENANT,
        work_order_id=WORK_ORDER,
        work_order_version=7,
        inspection_round=1,
        retry_count=attempt,
        work_order_snapshot={
            "id": str(WORK_ORDER),
            "tenant_id": str(TENANT),
            "version": 7,
            "status": "COMPLETED",
        },
        attachments_summary=(),
    )


class _Repository:
    def __init__(self, jobs: list[ClaimedQualityJob]) -> None:
        self.jobs = jobs
        self._claim_lock = asyncio.Lock()
        self.recoveries: list[tuple[UUID, datetime, datetime]] = []
        self.retries: list[tuple[UUID, int, str, datetime, datetime]] = []
        self.failures: list[tuple[UUID, int, str, datetime]] = []

    async def recover_expired(
        self,
        tenant_id: UUID,
        *,
        now: datetime,
        lease_expired_before: datetime,
    ) -> int:
        self.recoveries.append((tenant_id, now, lease_expired_before))
        return 0

    async def claim_quality_jobs(
        self,
        tenant_id: UUID,
        limit: int,
        *,
        now: datetime,
    ) -> list[ClaimedQualityJob]:
        async with self._claim_lock:
            claimed, self.jobs = self.jobs[:limit], self.jobs[limit:]
            return claimed

    async def retry_quality_job(
        self,
        job: ClaimedQualityJob,
        *,
        code: str,
        next_retry_at: datetime,
        now: datetime,
    ) -> bool:
        self.retries.append((job.id, job.retry_count, code, next_retry_at, now))
        return True

    async def fail_quality_job(
        self,
        job: ClaimedQualityJob,
        *,
        code: str,
        now: datetime,
    ) -> bool:
        self.failures.append((job.id, job.retry_count, code, now))
        return True


class _Processor:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[UUID] = []

    async def process(self, job: ClaimedQualityJob) -> QualityResultRecord:
        self.calls.append(job.id)
        if self.error is not None:
            raise self.error
        return _result(job)


def _result(job: ClaimedQualityJob) -> QualityResultRecord:
    return QualityResultRecord(
        id=UUID("44444444-4444-4444-4444-444444444444"),
        tenant_id=job.tenant_id,
        quality_job_id=job.id,
        work_order_id=job.work_order_id,
        work_order_version=job.work_order_version,
        inspection_round=job.inspection_round,
        verdict="SKIP",
        confidence=1.0,
        work_order_snapshot=job.work_order_snapshot,
        policy_versions={},
        attachment_summary=(),
        findings=(),
    )


@pytest.mark.asyncio
async def test_two_workers_claim_one_job_only_once() -> None:
    repository = _Repository([_job()])
    first_processor = _Processor()
    second_processor = _Processor()
    first = QualityWorker(repository, first_processor, clock=lambda: NOW)
    second = QualityWorker(repository, second_processor, clock=lambda: NOW)

    outcomes = await asyncio.gather(
        first.run_once(TENANT, limit=1),
        second.run_once(TENANT, limit=1),
    )

    assert sum(outcome.claimed for outcome in outcomes) == 1
    assert sum(outcome.succeeded for outcome in outcomes) == 1
    assert first_processor.calls + second_processor.calls == [JOB]


@pytest.mark.parametrize(
    ("attempt", "minutes"),
    [(1, 5), (2, 10), (3, 20), (4, 40), (5, 60), (8, 60)],
)
def test_retry_delay_is_bounded_exponential(attempt: int, minutes: int) -> None:
    assert retry_delay(attempt) == timedelta(minutes=minutes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ProviderTimeoutError(),
        ProviderRateLimitError(),
        ProviderUnavailableError(),
    ],
)
async def test_retryable_provider_errors_schedule_next_attempt(error: Exception) -> None:
    repository = _Repository([_job(attempt=1)])
    worker = QualityWorker(repository, _Processor(error), clock=lambda: NOW)

    outcome = await worker.run_once(TENANT, limit=1)

    assert (outcome.retried, outcome.failed) == (1, 0)
    assert repository.retries == [
        (JOB, 1, error.code, NOW + timedelta(minutes=5), NOW)  # type: ignore[attr-defined]
    ]
    assert repository.failures == []


@pytest.mark.asyncio
async def test_retryable_error_on_attempt_three_is_terminal() -> None:
    repository = _Repository([_job(attempt=3)])
    worker = QualityWorker(repository, _Processor(ProviderTimeoutError()), clock=lambda: NOW)

    outcome = await worker.run_once(TENANT, limit=1)

    assert (outcome.retried, outcome.failed) == (0, 1)
    assert repository.retries == []
    assert repository.failures == [(JOB, 3, "PROVIDER_TIMEOUT", NOW)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ProviderAuthError(), "PROVIDER_AUTH_FAILED"),
        (ProviderBadResponseError(), "PROVIDER_BAD_RESPONSE"),
        (QualityOutputError("bad schema"), "QUALITY_OUTPUT_INVALID"),
        (ValueError("missing required input"), "QUALITY_INPUT_INVALID"),
    ],
)
async def test_nonretryable_errors_fail_immediately(error: Exception, code: str) -> None:
    repository = _Repository([_job(attempt=1)])
    worker = QualityWorker(repository, _Processor(error), clock=lambda: NOW)

    outcome = await worker.run_once(TENANT, limit=1)

    assert (outcome.retried, outcome.failed) == (0, 1)
    assert repository.failures == [(JOB, 1, code, NOW)]


@pytest.mark.asyncio
async def test_unknown_processor_crash_leaves_running_job_for_lease_recovery() -> None:
    repository = _Repository([_job(attempt=1)])
    worker = QualityWorker(
        repository,
        _Processor(RuntimeError("synthetic crash")),
        clock=lambda: NOW,
        lease_timeout=timedelta(minutes=15),
    )

    with pytest.raises(RuntimeError, match="synthetic crash"):
        await worker.run_once(TENANT, limit=1)

    assert repository.retries == []
    assert repository.failures == []
    assert repository.recoveries == [(TENANT, NOW, NOW - timedelta(minutes=15))]


def test_worker_rejects_invalid_limit_and_naive_clock() -> None:
    worker = QualityWorker(_Repository([]), _Processor(), clock=lambda: NOW.replace(tzinfo=None))

    with pytest.raises(ValueError, match="between 1 and 20"):
        asyncio.run(worker.run_once(TENANT, limit=21))
    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(worker.run_once(TENANT, limit=1))


@pytest.mark.asyncio
async def test_lifecycle_runs_and_cancels_processor_and_callback_loops_independently() -> None:
    entered = [asyncio.Event(), asyncio.Event()]

    class BlockingWorker:
        def __init__(self, index: int) -> None:
            self.index = index

        async def run_once(self, tenant_id: UUID, limit: int) -> object:
            entered[self.index].set()
            await asyncio.Event().wait()

    processor_loop = TenantWorkerLoop(
        BlockingWorker(0),
        tenant_ids=(TENANT,),
        task_name="synthetic-quality-processor",
        poll_interval_seconds=60,
    )
    callback_loop = TenantWorkerLoop(
        BlockingWorker(1),
        tenant_ids=(TENANT,),
        task_name="synthetic-quality-callback",
        poll_interval_seconds=60,
    )
    lifecycle = QualityLifecycle(worker_loops=(processor_loop, callback_loop))

    await lifecycle.start()
    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), timeout=0.5)
    assert processor_loop.running is True
    assert callback_loop.running is True

    await lifecycle.close()

    assert processor_loop.running is False
    assert callback_loop.running is False
