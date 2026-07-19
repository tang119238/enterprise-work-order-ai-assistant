from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Database
from app.knowledge.embedding.base import EMBEDDING_DIMENSIONS, normalize_embeddings
from app.knowledge.models import (
    ChunkDraft,
    ClaimedEmbeddingJob,
    DocumentDraft,
    IngestResult,
)

_IDENTITY_NAMESPACE = UUID("2869d879-62b7-51ac-8ff5-e21ee6cae454")
_MAX_CLAIM_LIMIT = 100


class PostgresKnowledgeRepository:
    """Tenant-scoped version and embedding-job persistence.

    Each public method opens one short database transaction. Embedding inference is
    intentionally absent from this class so workers never hold a transaction while
    performing model or network I/O.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_version(
        self,
        tenant_id: UUID,
        document: DocumentDraft,
        chunks: Sequence[ChunkDraft],
        model_key: str,
    ) -> IngestResult:
        _require_tenant(tenant_id)
        _require_model_key(model_key)
        _require_unique_chunks(chunks)
        tenant_value = tenant_id
        async with self._database.session(tenant_id) as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"{tenant_id}:{document.document_key}"},
            )
            latest_result = await session.execute(
                text(
                    """
                    SELECT id, version, content_hash
                    FROM knowledge_document
                    WHERE tenant_id = :tenant_id
                      AND document_key = :document_key
                    ORDER BY version DESC
                    LIMIT 1
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": tenant_value,
                    "document_key": document.document_key,
                },
            )
            latest = latest_result.mappings().first()
            if latest is not None and latest["content_hash"] == document.content_hash:
                return IngestResult(
                    document_id=latest["id"],
                    version=latest["version"],
                    chunk_count=0,
                    skipped=True,
                )

            version = int(latest["version"]) + 1 if latest is not None else 1
            document_id = _stable_uuid(tenant_id, document.document_key, str(version))
            await session.execute(
                text(
                    """
                    INSERT INTO knowledge_document (
                        id, tenant_id, document_key, title, source_type, source_uri,
                        content_hash, version, status
                    ) VALUES (
                        :id, :tenant_id, :document_key, :title, 'MARKDOWN', :source_uri,
                        :content_hash, :version, 'PENDING'
                    )
                    """
                ),
                {
                    "id": document_id,
                    "tenant_id": tenant_value,
                    "document_key": document.document_key,
                    "title": document.title,
                    "source_uri": document.source_uri,
                    "content_hash": document.content_hash,
                    "version": version,
                },
            )

            chunk_rows: list[dict[str, object]] = []
            job_rows: list[dict[str, object]] = []
            for chunk in chunks:
                chunk_id = _stable_uuid(
                    tenant_id,
                    document.document_key,
                    str(version),
                    chunk.chunk_key,
                )
                business_key = _business_key(
                    tenant_id,
                    document.document_key,
                    version,
                    chunk.chunk_key,
                    model_key,
                )
                chunk_rows.append(
                    {
                        "id": chunk_id,
                        "tenant_id": tenant_value,
                        "document_id": document_id,
                        "chunk_key": chunk.chunk_key,
                        "section": chunk.section,
                        "content": chunk.content,
                        "content_hash": chunk.content_hash,
                        "token_count": chunk.token_count,
                        "ordinal": chunk.ordinal,
                    }
                )
                job_rows.append(
                    {
                        "id": _stable_uuid(tenant_id, "embedding-job", business_key),
                        "tenant_id": tenant_value,
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "business_key": business_key,
                        "model_key": model_key,
                    }
                )

            await session.execute(
                text(
                    """
                    INSERT INTO knowledge_chunk (
                        id, tenant_id, document_id, chunk_key, section, content,
                        content_hash, token_count, ordinal, status
                    ) VALUES (
                        :id, :tenant_id, :document_id, :chunk_key, :section, :content,
                        :content_hash, :token_count, :ordinal, 'PENDING'
                    )
                    ON CONFLICT (tenant_id, document_id, chunk_key) DO NOTHING
                    """
                ),
                chunk_rows,
            )
            await session.execute(
                text(
                    """
                    INSERT INTO embedding_job (
                        id, tenant_id, document_id, chunk_id, business_key, model_key,
                        status, retry_count
                    ) VALUES (
                        :id, :tenant_id, :document_id, :chunk_id, :business_key, :model_key,
                        'PENDING', 0
                    )
                    ON CONFLICT (tenant_id, business_key) DO NOTHING
                    """
                ),
                job_rows,
            )
            return IngestResult(
                document_id=document_id,
                version=version,
                chunk_count=len(chunks),
                skipped=False,
            )

    async def claim(
        self,
        tenant_id: UUID,
        limit: int,
        *,
        model_key: str,
        now: datetime | None = None,
    ) -> list[ClaimedEmbeddingJob]:
        _require_tenant(tenant_id)
        _require_model_key(model_key)
        if isinstance(limit, bool) or not 1 <= limit <= _MAX_CLAIM_LIMIT:
            raise ValueError("limit must be between 1 and 100")
        claimed_at = _aware_time(now)
        async with self._database.session(tenant_id) as session:
            result = await session.execute(
                text(
                    """
                    WITH candidates AS (
                        SELECT j.id, j.status AS previous_status, j.document_id,
                               j.chunk_id, j.model_key, j.retry_count,
                               c.content, c.content_hash
                        FROM embedding_job AS j
                        JOIN knowledge_chunk AS c
                          ON c.tenant_id = j.tenant_id
                         AND c.id = j.chunk_id
                        WHERE j.tenant_id = :tenant_id
                          AND j.model_key = :model_key
                          AND (
                              j.status = 'PENDING'
                              OR (
                                  j.status = 'RETRY_WAIT'
                                  AND j.next_retry_at <= :now
                              )
                          )
                        ORDER BY COALESCE(j.next_retry_at, j.created_at), j.created_at, j.id
                        FOR UPDATE OF j SKIP LOCKED
                        LIMIT :limit
                    ), claimed AS (
                        UPDATE embedding_job AS j
                        SET status = 'RUNNING', started_at = :now,
                            finished_at = NULL, error_code = NULL,
                            next_retry_at = NULL, updated_at = :now
                        FROM candidates
                        WHERE j.tenant_id = :tenant_id
                          AND j.id = candidates.id
                          AND j.status = candidates.previous_status
                        RETURNING j.id, j.tenant_id, j.document_id, j.chunk_id,
                                  j.model_key, j.retry_count
                    )
                    SELECT claimed.id, claimed.tenant_id, claimed.document_id,
                           claimed.chunk_id, claimed.model_key, claimed.retry_count,
                           candidates.content, candidates.content_hash
                    FROM claimed
                    JOIN candidates ON candidates.id = claimed.id
                    ORDER BY claimed.id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "model_key": model_key,
                    "limit": limit,
                    "now": claimed_at,
                },
            )
            return [ClaimedEmbeddingJob.model_validate(row) for row in result.mappings().all()]

    async def succeed(
        self,
        tenant_id: UUID,
        job_id: UUID,
        vector: Sequence[object],
        *,
        expected_content_hash: str,
        now: datetime | None = None,
    ) -> bool:
        _require_tenant(tenant_id)
        if not isinstance(job_id, UUID):
            raise TypeError("job_id must be a UUID")
        _require_content_hash(expected_content_hash)
        completed_at = _aware_time(now)
        normalized = normalize_embeddings([vector], expected_count=1)[0]
        async with self._database.session(tenant_id) as session:
            locked_result = await session.execute(
                text(
                    """
                    SELECT j.document_id, d.document_key, d.version, j.chunk_id,
                           j.model_key, c.content_hash
                    FROM embedding_job AS j
                    JOIN knowledge_document AS d
                      ON d.tenant_id = j.tenant_id
                     AND d.id = j.document_id
                    JOIN knowledge_chunk AS c
                      ON c.tenant_id = j.tenant_id
                     AND c.id = j.chunk_id
                    WHERE j.tenant_id = :tenant_id
                      AND j.id = :job_id
                      AND j.status = 'RUNNING'
                      AND c.content_hash = :expected_content_hash
                    FOR UPDATE OF d, j
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "job_id": job_id,
                    "expected_content_hash": expected_content_hash,
                },
            )
            job = locked_result.mappings().first()
            if job is None:
                return False

            await session.execute(
                text(
                    """
                    INSERT INTO knowledge_embedding (
                        tenant_id, chunk_id, model_key, dimensions,
                        embedding, content_hash, embedded_at
                    ) VALUES (
                        :tenant_id, :chunk_id, :model_key, :dimensions,
                        CAST(:embedding AS vector), :content_hash, :now
                    )
                    ON CONFLICT (tenant_id, chunk_id, model_key) DO UPDATE
                    SET dimensions = EXCLUDED.dimensions,
                        embedding = EXCLUDED.embedding,
                        content_hash = EXCLUDED.content_hash,
                        embedded_at = EXCLUDED.embedded_at,
                        updated_at = :now
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "chunk_id": job["chunk_id"],
                    "model_key": job["model_key"],
                    "dimensions": EMBEDDING_DIMENSIONS,
                    "embedding": json.dumps(normalized, separators=(",", ":")),
                    "content_hash": job["content_hash"],
                    "now": completed_at,
                },
            )
            cas_result = await session.execute(
                text(
                    """
                    UPDATE embedding_job
                    SET status = 'SUCCEEDED', finished_at = :now,
                        error_code = NULL, next_retry_at = NULL, updated_at = :now
                    WHERE tenant_id = :tenant_id
                      AND id = :job_id
                      AND status = 'RUNNING'
                    RETURNING id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "job_id": job_id,
                    "now": completed_at,
                },
            )
            if cas_result.scalar_one_or_none() is None:
                return False

            unfinished_result = await session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM embedding_job
                    WHERE tenant_id = :tenant_id
                      AND document_id = :document_id
                      AND status <> 'SUCCEEDED'
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "document_id": job["document_id"],
                },
            )
            if int(unfinished_result.scalar_one()) != 0:
                return True

            latest_result = await session.execute(
                text(
                    """
                    SELECT NOT EXISTS (
                        SELECT 1
                        FROM knowledge_document
                        WHERE tenant_id = :tenant_id
                          AND document_key = :document_key
                          AND version > :version
                    )
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "document_key": job["document_key"],
                    "version": job["version"],
                },
            )
            if bool(latest_result.scalar_one()):
                await self._activate_latest(session, tenant_id, job, completed_at)
            else:
                await self._keep_stale_inactive(session, tenant_id, job, completed_at)
            return True

    async def retry(
        self,
        tenant_id: UUID,
        job_id: UUID,
        *,
        code: str,
        next_retry_at: datetime,
        now: datetime | None = None,
    ) -> bool:
        _require_tenant(tenant_id)
        if not isinstance(job_id, UUID):
            raise TypeError("job_id must be a UUID")
        _require_error_code(code)
        attempted_at = _aware_time(now)
        retry_at = _aware_time(next_retry_at)
        if retry_at <= attempted_at:
            raise ValueError("next_retry_at must be later than now")
        async with self._database.session(tenant_id) as session:
            result = await session.execute(
                text(
                    """
                    UPDATE embedding_job
                    SET status = 'RETRY_WAIT', retry_count = retry_count + 1,
                        error_code = :code, next_retry_at = :next_retry_at,
                        started_at = NULL, finished_at = NULL, updated_at = :now
                    WHERE tenant_id = :tenant_id
                      AND id = :job_id
                      AND status = 'RUNNING'
                    RETURNING id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "job_id": job_id,
                    "code": code,
                    "next_retry_at": retry_at,
                    "now": attempted_at,
                },
            )
            return result.scalar_one_or_none() is not None

    async def fail(
        self,
        tenant_id: UUID,
        job_id: UUID,
        *,
        code: str,
        now: datetime | None = None,
    ) -> bool:
        _require_tenant(tenant_id)
        if not isinstance(job_id, UUID):
            raise TypeError("job_id must be a UUID")
        _require_error_code(code)
        failed_at = _aware_time(now)
        async with self._database.session(tenant_id) as session:
            result = await session.execute(
                text(
                    """
                    UPDATE embedding_job
                    SET status = 'FAILED', error_code = :code,
                        next_retry_at = NULL, finished_at = :now, updated_at = :now
                    WHERE tenant_id = :tenant_id
                      AND id = :job_id
                      AND status = 'RUNNING'
                    RETURNING id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "job_id": job_id,
                    "code": code,
                    "now": failed_at,
                },
            )
            return result.scalar_one_or_none() is not None

    @staticmethod
    async def _activate_latest(
        session: AsyncSession,
        tenant_id: UUID,
        job: RowMapping,
        now: datetime,
    ) -> None:
        parameters = {
            "tenant_id": tenant_id,
            "document_id": job["document_id"],
            "document_key": job["document_key"],
            "version": job["version"],
            "now": now,
        }
        await session.execute(
            text(
                """
                UPDATE knowledge_chunk
                SET status = 'INACTIVE', updated_at = :now
                WHERE tenant_id = :tenant_id
                  AND document_id IN (
                      SELECT id FROM knowledge_document
                      WHERE tenant_id = :tenant_id
                        AND document_key = :document_key
                        AND version < :version
                  )
                """
            ),
            parameters,
        )
        await session.execute(
            text(
                """
                UPDATE knowledge_document
                SET status = 'INACTIVE', updated_at = :now
                WHERE tenant_id = :tenant_id
                  AND document_key = :document_key
                  AND version < :version
                """
            ),
            parameters,
        )
        await session.execute(
            text(
                """
                UPDATE knowledge_chunk
                SET status = 'ACTIVE', updated_at = :now
                WHERE tenant_id = :tenant_id
                  AND document_id = :document_id
                """
            ),
            parameters,
        )
        await session.execute(
            text(
                """
                UPDATE knowledge_document
                SET status = 'ACTIVE', updated_at = :now
                WHERE tenant_id = :tenant_id
                  AND id = :document_id
                """
            ),
            parameters,
        )

    @staticmethod
    async def _keep_stale_inactive(
        session: AsyncSession,
        tenant_id: UUID,
        job: RowMapping,
        now: datetime,
    ) -> None:
        parameters = {
            "tenant_id": tenant_id,
            "document_id": job["document_id"],
            "now": now,
        }
        await session.execute(
            text(
                """
                UPDATE knowledge_chunk
                SET status = 'INACTIVE', updated_at = :now
                WHERE tenant_id = :tenant_id
                  AND document_id = :document_id
                """
            ),
            parameters,
        )
        await session.execute(
            text(
                """
                UPDATE knowledge_document
                SET status = 'INACTIVE', updated_at = :now
                WHERE tenant_id = :tenant_id
                  AND id = :document_id
                """
            ),
            parameters,
        )


def _stable_uuid(tenant_id: UUID, *parts: str) -> UUID:
    return uuid5(_IDENTITY_NAMESPACE, "\0".join((str(tenant_id), *parts)))


def _business_key(
    tenant_id: UUID,
    document_key: str,
    version: int,
    chunk_key: str,
    model_key: str,
) -> str:
    source = "\0".join(
        (str(tenant_id), document_key, str(version), chunk_key, model_key)
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _require_tenant(tenant_id: UUID) -> None:
    if not isinstance(tenant_id, UUID):
        raise TypeError("tenant_id must be a UUID")


def _require_model_key(model_key: str) -> None:
    if (
        not model_key
        or model_key.strip() != model_key
        or len(model_key) > 200
        or "\x00" in model_key
    ):
        raise ValueError("model_key must be nonblank and at most 200 characters")


def _require_error_code(code: str) -> None:
    if not code or code.strip() != code or len(code) > 128 or "\x00" in code:
        raise ValueError("code must be a stable nonblank error code")


def _require_content_hash(content_hash: str) -> None:
    if len(content_hash) != 64 or any(
        character not in "0123456789abcdef" for character in content_hash
    ):
        raise ValueError("content_hash must be a lowercase SHA-256 digest")


def _require_unique_chunks(chunks: Sequence[ChunkDraft]) -> None:
    if not chunks:
        raise ValueError("at least one knowledge chunk is required")
    chunk_keys = [chunk.chunk_key for chunk in chunks]
    ordinals = [chunk.ordinal for chunk in chunks]
    if len(set(chunk_keys)) != len(chunks) or len(set(ordinals)) != len(chunks):
        raise ValueError("chunk keys and ordinals must be unique")


def _aware_time(value: datetime | None) -> datetime:
    resolved = value or datetime.now(UTC)
    if resolved.tzinfo is None or resolved.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return resolved
