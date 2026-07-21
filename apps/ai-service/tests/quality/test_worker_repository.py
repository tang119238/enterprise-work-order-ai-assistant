from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from app.quality.repository import PostgresQualityRepository

TENANT = UUID("11111111-1111-1111-1111-111111111111")
WORK_ORDER = UUID("22222222-2222-2222-2222-222222222222")
JOB = UUID("33333333-3333-3333-3333-333333333333")
RESULT = UUID("44444444-4444-4444-4444-444444444444")
NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260720_03_quality_callback_delivery.py"
)


class _Mappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self.rows

    def first(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: object = None) -> None:
        self.rows = rows or []
        self.scalar = scalar

    def mappings(self) -> _Mappings:
        return _Mappings(self.rows)

    def first(self) -> object | None:
        return self.rows[0] if self.rows else None

    def scalar_one(self) -> object:
        return self.scalar


class _Session:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def execute(self, statement: object, parameters: object) -> _Result:
        sql = " ".join(str(statement).split())
        self.calls.append((sql, parameters))
        if "WITH candidates AS" in sql:
            return _Result(
                [
                    {
                        "id": JOB,
                        "tenant_id": TENANT,
                        "work_order_id": WORK_ORDER,
                        "work_order_version": 7,
                        "inspection_round": 1,
                        "retry_count": 1,
                        "trigger_payload": {
                            "work_order_snapshot": {
                                "id": str(WORK_ORDER),
                                "tenant_id": str(TENANT),
                                "version": 7,
                                "status": "COMPLETED",
                            },
                            "attachments_summary": [],
                        },
                    }
                ]
            )
        if "QUALITY_WORKER_LEASE_EXPIRED" in sql:
            return _Result([{"id": JOB}, {"id": UUID(int=5)}])
        if "FROM quality_result" in sql and "callback_at IS NULL" in sql:
            return _Result([])
        if "mark_quality_result_callback_delivered" in sql:
            return _Result(scalar=True)
        return _Result([{"id": JOB}])


class _Database:
    def __init__(self) -> None:
        self.session_instance = _Session()

    @asynccontextmanager
    async def session(self, tenant_id: UUID):  # type: ignore[no-untyped-def]
        assert tenant_id == TENANT
        yield self.session_instance


@pytest.mark.asyncio
async def test_claim_uses_skip_locked_cas_and_increments_attempt() -> None:
    database = _Database()
    repository = PostgresQualityRepository(database)  # type: ignore[arg-type]

    jobs = await repository.claim_quality_jobs(TENANT, 10, now=NOW)

    assert len(jobs) == 1
    assert jobs[0].retry_count == 1
    sql, parameters = database.session_instance.calls[0]
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "status = 'PENDING'" in sql
    assert "status = 'RETRY_WAIT' AND next_retry_at <= :now" in sql
    assert "retry_count = job.retry_count + 1" in sql
    assert parameters == {"tenant_id": TENANT, "limit": 10, "now": NOW}


@pytest.mark.asyncio
async def test_expired_running_lease_is_recovered_without_exceeding_attempt_limit() -> None:
    database = _Database()
    repository = PostgresQualityRepository(database)  # type: ignore[arg-type]

    recovered = await repository.recover_expired(
        TENANT,
        now=NOW,
        lease_expired_before=NOW - timedelta(minutes=15),
    )

    assert recovered == 2
    sql, _ = database.session_instance.calls[0]
    assert "WHEN retry_count >= max_retry_count THEN 'FAILED'" in sql
    assert "ELSE 'RETRY_WAIT'" in sql
    assert "status = 'RUNNING'" in sql


@pytest.mark.asyncio
async def test_callback_selection_ignores_job_state_and_marks_through_narrow_function() -> None:
    database = _Database()
    repository = PostgresQualityRepository(database)  # type: ignore[arg-type]

    assert await repository.pending_callbacks(TENANT, 10) == []
    assert await repository.mark_callback_delivered(TENANT, RESULT) is True

    select_sql = database.session_instance.calls[0][0]
    mark_sql = database.session_instance.calls[1][0]
    assert "callback_at IS NULL" in select_sql
    assert "JOIN quality_job" not in select_sql
    assert "FROM quality_job" not in select_sql
    assert "mark_quality_result_callback_delivered" in mark_sql


def test_callback_migration_preserves_append_only_table_grant() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert "SECURITY DEFINER" in source
    assert "current_setting('app.tenant_id'" in source
    assert "callback_at IS NULL" in source
    assert "REVOKE ALL ON FUNCTION" in source
    assert "GRANT EXECUTE ON FUNCTION" in source
    assert "GRANT UPDATE ON TABLE quality_result" not in source
