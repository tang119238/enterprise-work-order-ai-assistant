from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Self
from uuid import UUID, uuid4

import pytest

from app.knowledge.models import ChunkDraft, DocumentDraft
from app.knowledge.repository import PostgresKnowledgeRepository

TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")
NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


class ScriptedResult:
    def __init__(
        self,
        *,
        rows: Sequence[dict[str, Any]] = (),
        scalar: object | None = None,
    ) -> None:
        self._rows = list(rows)
        self._scalar = scalar

    def mappings(self) -> Self:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def scalar_one(self) -> object:
        assert self._scalar is not None
        return self._scalar

    def scalar_one_or_none(self) -> object | None:
        return self._scalar


class ScriptedSession:
    def __init__(self, results: Sequence[ScriptedResult]) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, object | None]] = []

    async def execute(
        self,
        statement: object,
        parameters: object | None = None,
    ) -> ScriptedResult:
        self.calls.append((str(statement), parameters))
        assert self._results, f"unexpected SQL: {statement}"
        return self._results.pop(0)


class ScriptedDatabase:
    def __init__(self, results: Sequence[ScriptedResult]) -> None:
        self.session_instance = ScriptedSession(results)
        self.tenant_ids: list[UUID] = []

    @asynccontextmanager
    async def session(self, tenant_id: UUID) -> AsyncIterator[ScriptedSession]:
        self.tenant_ids.append(tenant_id)
        yield self.session_instance


def document(content_hash: str = "a" * 64) -> DocumentDraft:
    return DocumentDraft(
        document_key="rework-policy",
        title="返工规则",
        source_uri="policy://rework",
        content_hash=content_hash,
    )


def chunks() -> list[ChunkDraft]:
    return [
        ChunkDraft(
            chunk_key="rework-policy:0:0",
            section="适用范围",
            content="返工单必须关联根工单。",
            content_hash="b" * 64,
            token_count=11,
            ordinal=0,
        ),
        ChunkDraft(
            chunk_key="rework-policy:1:0",
            section="时限",
            content="整改必须在两个工作日内完成。",
            content_hash="c" * 64,
            token_count=14,
            ordinal=1,
        ),
    ]


@pytest.mark.asyncio
async def test_create_version_serializes_per_document_and_skips_unchanged_content() -> None:
    database = ScriptedDatabase(
        [
            ScriptedResult(),
            ScriptedResult(rows=[{"id": uuid4(), "version": 3, "content_hash": "a" * 64}]),
        ]
    )
    repository = PostgresKnowledgeRepository(database)

    result = await repository.create_version(
        TENANT_ID,
        document(),
        chunks(),
        model_key="BAAI/bge-small-zh-v1.5",
    )

    assert result.skipped is True
    assert result.version == 3
    assert result.chunk_count == 0
    assert len(database.session_instance.calls) == 2
    lock_sql, lock_parameters = database.session_instance.calls[0]
    latest_sql, latest_parameters = database.session_instance.calls[1]
    assert "pg_advisory_xact_lock" in lock_sql
    assert "tenant_id = :tenant_id" in latest_sql
    assert "document_key = :document_key" in latest_sql
    assert "FOR UPDATE" in latest_sql
    assert lock_parameters == {"lock_key": f"{TENANT_ID}:rework-policy"}
    assert latest_parameters == {
        "tenant_id": TENANT_ID,
        "document_key": "rework-policy",
    }


@pytest.mark.asyncio
async def test_create_version_inserts_next_version_unique_chunks_and_idempotent_jobs() -> None:
    database = ScriptedDatabase(
        [
            ScriptedResult(),
            ScriptedResult(rows=[{"id": uuid4(), "version": 3, "content_hash": "d" * 64}]),
            ScriptedResult(),
            ScriptedResult(),
            ScriptedResult(),
        ]
    )
    repository = PostgresKnowledgeRepository(database)

    result = await repository.create_version(
        TENANT_ID,
        document(),
        chunks(),
        model_key="BAAI/bge-small-zh-v1.5",
    )

    assert result.skipped is False
    assert result.version == 4
    assert result.chunk_count == 2
    insert_document_sql, insert_document_parameters = database.session_instance.calls[2]
    insert_chunks_sql, insert_chunk_parameters = database.session_instance.calls[3]
    insert_jobs_sql, insert_job_parameters = database.session_instance.calls[4]
    assert "INSERT INTO knowledge_document" in insert_document_sql
    assert "'PENDING'" in insert_document_sql
    assert insert_document_parameters["version"] == 4  # type: ignore[index]
    assert isinstance(insert_document_parameters["id"], UUID)  # type: ignore[index]
    assert "INSERT INTO knowledge_chunk" in insert_chunks_sql
    assert "ON CONFLICT" in insert_chunks_sql
    assert "INSERT INTO embedding_job" in insert_jobs_sql
    assert "ON CONFLICT" in insert_jobs_sql
    assert isinstance(insert_chunk_parameters, list)
    assert isinstance(insert_job_parameters, list)
    assert len({row["chunk_key"] for row in insert_chunk_parameters}) == 2
    assert len({row["business_key"] for row in insert_job_parameters}) == 2
    assert all(len(row["business_key"]) == 64 for row in insert_job_parameters)
    assert all(row["tenant_id"] == TENANT_ID for row in insert_job_parameters)
    assert all(isinstance(row["id"], UUID) for row in insert_chunk_parameters)
    assert all(isinstance(row["id"], UUID) for row in insert_job_parameters)


@pytest.mark.asyncio
async def test_claim_is_tenant_scoped_due_retry_cas_and_skip_locked() -> None:
    job_id = uuid4()
    document_id = uuid4()
    chunk_id = uuid4()
    database = ScriptedDatabase(
        [
            ScriptedResult(
                rows=[
                    {
                        "id": job_id,
                        "tenant_id": TENANT_ID,
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "model_key": "synthetic-model",
                        "content": "整改规则",
                        "content_hash": "a" * 64,
                        "retry_count": 1,
                    }
                ]
            )
        ]
    )
    repository = PostgresKnowledgeRepository(database)

    claimed = await repository.claim(
        TENANT_ID,
        limit=5,
        model_key="synthetic-model",
        now=NOW,
    )

    assert [job.id for job in claimed] == [job_id]
    sql, parameters = database.session_instance.calls[0]
    assert "FOR UPDATE OF j SKIP LOCKED" in sql
    assert "j.status = candidates.previous_status" in sql
    assert "j.tenant_id = :tenant_id" in sql
    assert "j.model_key = :model_key" in sql
    assert "next_retry_at <= :now" in sql
    assert parameters == {
        "tenant_id": TENANT_ID,
        "model_key": "synthetic-model",
        "limit": 5,
        "now": NOW,
    }


@pytest.mark.asyncio
async def test_succeed_is_cas_and_does_not_activate_partial_document() -> None:
    job_id = uuid4()
    document_id = uuid4()
    chunk_id = uuid4()
    database = ScriptedDatabase(
        [
            ScriptedResult(
                rows=[
                    {
                        "document_id": document_id,
                        "document_key": "rework-policy",
                        "version": 2,
                        "chunk_id": chunk_id,
                        "model_key": "synthetic-model",
                        "content_hash": "a" * 64,
                    }
                ]
            ),
            ScriptedResult(),
            ScriptedResult(scalar=job_id),
            ScriptedResult(scalar=1),
        ]
    )
    repository = PostgresKnowledgeRepository(database)

    succeeded = await repository.succeed(
        TENANT_ID,
        job_id,
        [1.0] + [0.0] * 511,
        expected_content_hash="a" * 64,
        now=NOW,
    )

    assert succeeded is True
    assert len(database.session_instance.calls) == 4
    lock_sql, _ = database.session_instance.calls[0]
    upsert_sql, upsert_parameters = database.session_instance.calls[1]
    cas_sql, cas_parameters = database.session_instance.calls[2]
    unfinished_sql, _ = database.session_instance.calls[3]
    assert "FOR UPDATE OF d, j" in lock_sql
    assert "c.content_hash = :expected_content_hash" in lock_sql
    assert "ON CONFLICT (tenant_id, chunk_id, model_key) DO UPDATE" in upsert_sql
    assert upsert_parameters["dimensions"] == 512  # type: ignore[index]
    assert "status = 'SUCCEEDED'" in cas_sql
    assert "status = 'RUNNING'" in cas_sql
    assert cas_parameters["job_id"] == job_id  # type: ignore[index]
    assert "status <> 'SUCCEEDED'" in unfinished_sql


@pytest.mark.asyncio
async def test_last_success_activates_latest_version_and_inactivates_previous_versions() -> None:
    job_id = uuid4()
    document_id = uuid4()
    chunk_id = uuid4()
    database = ScriptedDatabase(
        [
            ScriptedResult(
                rows=[
                    {
                        "document_id": document_id,
                        "document_key": "rework-policy",
                        "version": 2,
                        "chunk_id": chunk_id,
                        "model_key": "synthetic-model",
                        "content_hash": "a" * 64,
                    }
                ]
            ),
            ScriptedResult(),
            ScriptedResult(scalar=job_id),
            ScriptedResult(scalar=0),
            ScriptedResult(scalar=True),
            ScriptedResult(),
            ScriptedResult(),
            ScriptedResult(),
            ScriptedResult(),
        ]
    )
    repository = PostgresKnowledgeRepository(database)

    assert await repository.succeed(
        TENANT_ID,
        job_id,
        [1.0] + [0.0] * 511,
        expected_content_hash="a" * 64,
        now=NOW,
    )

    activation_sql = "\n".join(sql for sql, _ in database.session_instance.calls[5:])
    assert "status = 'INACTIVE'" in activation_sql
    assert "status = 'ACTIVE'" in activation_sql
    assert "version < :version" in activation_sql
    assert "document_id = :document_id" in activation_sql


@pytest.mark.asyncio
async def test_completed_old_version_remains_inactive_when_newer_version_exists() -> None:
    job_id = uuid4()
    document_id = uuid4()
    chunk_id = uuid4()
    database = ScriptedDatabase(
        [
            ScriptedResult(
                rows=[
                    {
                        "document_id": document_id,
                        "document_key": "rework-policy",
                        "version": 1,
                        "chunk_id": chunk_id,
                        "model_key": "synthetic-model",
                        "content_hash": "a" * 64,
                    }
                ]
            ),
            ScriptedResult(),
            ScriptedResult(scalar=job_id),
            ScriptedResult(scalar=0),
            ScriptedResult(scalar=False),
            ScriptedResult(),
            ScriptedResult(),
        ]
    )
    repository = PostgresKnowledgeRepository(database)

    assert await repository.succeed(
        TENANT_ID,
        job_id,
        [1.0] + [0.0] * 511,
        expected_content_hash="a" * 64,
        now=NOW,
    )

    final_sql = "\n".join(sql for sql, _ in database.session_instance.calls[5:])
    assert "status = 'INACTIVE'" in final_sql
    assert "status = 'ACTIVE'" not in final_sql


@pytest.mark.asyncio
async def test_retry_is_running_to_retry_wait_cas_with_stable_error_code() -> None:
    job_id = uuid4()
    retry_at = NOW + timedelta(minutes=5)
    database = ScriptedDatabase([ScriptedResult(scalar=job_id)])
    repository = PostgresKnowledgeRepository(database)

    retried = await repository.retry(
        TENANT_ID,
        job_id,
        code="EMBEDDING_PROVIDER_UNAVAILABLE",
        next_retry_at=retry_at,
        now=NOW,
    )

    assert retried is True
    sql, parameters = database.session_instance.calls[0]
    assert "status = 'RETRY_WAIT'" in sql
    assert "status = 'RUNNING'" in sql
    assert "retry_count = retry_count + 1" in sql
    assert parameters == {
        "tenant_id": TENANT_ID,
        "job_id": job_id,
        "code": "EMBEDDING_PROVIDER_UNAVAILABLE",
        "next_retry_at": retry_at,
        "now": NOW,
    }


@pytest.mark.asyncio
async def test_fail_is_running_to_failed_cas_for_nonretryable_errors() -> None:
    job_id = uuid4()
    database = ScriptedDatabase([ScriptedResult(scalar=job_id)])
    repository = PostgresKnowledgeRepository(database)

    failed = await repository.fail(
        TENANT_ID,
        job_id,
        code="EMBEDDING_VECTOR_INVALID",
        now=NOW,
    )

    assert failed is True
    sql, parameters = database.session_instance.calls[0]
    assert "status = 'FAILED'" in sql
    assert "status = 'RUNNING'" in sql
    assert parameters == {
        "tenant_id": TENANT_ID,
        "job_id": job_id,
        "code": "EMBEDDING_VECTOR_INVALID",
        "now": NOW,
    }
