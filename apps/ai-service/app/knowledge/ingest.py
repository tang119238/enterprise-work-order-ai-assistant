from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.knowledge.embedding.base import EmbeddingError, EmbeddingProvider
from app.knowledge.loader import chunk_policy_markdown
from app.knowledge.models import (
    ChunkDraft,
    ClaimedEmbeddingJob,
    DocumentDraft,
    IngestResult,
    WorkerRunResult,
)

_TOKEN_PATTERN = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]")


class IngestRepository(Protocol):
    async def create_version(
        self,
        tenant_id: UUID,
        document: DocumentDraft,
        chunks: Sequence[ChunkDraft],
        model_key: str,
    ) -> IngestResult: ...


class WorkerRepository(Protocol):
    async def claim(
        self,
        tenant_id: UUID,
        limit: int,
        *,
        model_key: str,
        now: datetime,
    ) -> list[ClaimedEmbeddingJob]: ...

    async def succeed(
        self,
        tenant_id: UUID,
        job_id: UUID,
        vector: Sequence[object],
        *,
        expected_content_hash: str,
        now: datetime,
    ) -> bool: ...

    async def retry(
        self,
        tenant_id: UUID,
        job_id: UUID,
        *,
        code: str,
        next_retry_at: datetime,
        now: datetime,
    ) -> bool: ...

    async def fail(
        self,
        tenant_id: UUID,
        job_id: UUID,
        *,
        code: str,
        now: datetime,
    ) -> bool: ...


class KnowledgeIngestor:
    def __init__(self, repository: IngestRepository, *, model_key: str) -> None:
        if (
            not model_key
            or model_key.strip() != model_key
            or len(model_key) > 200
            or "\x00" in model_key
        ):
            raise ValueError("model_key must be nonblank and at most 200 characters")
        self._repository = repository
        self._model_key = model_key

    async def ingest(
        self,
        tenant_id: UUID,
        document_key: str,
        title: str,
        markdown: str,
        source_uri: str,
    ) -> IngestResult:
        _validate_document_input(tenant_id, document_key, title, markdown, source_uri)
        policy_chunks = chunk_policy_markdown(document_key, title, markdown)
        if not policy_chunks:
            raise ValueError("markdown must contain at least one nonblank H2 or H3 section")
        if any(len(chunk.section) > 500 for chunk in policy_chunks):
            raise ValueError("markdown section headings must be at most 500 characters")
        if any(len(chunk.chunk_id) > 200 for chunk in policy_chunks):
            raise ValueError("generated chunk keys must be at most 200 characters")
        chunks = [
            ChunkDraft(
                chunk_key=chunk.chunk_id,
                section=chunk.section,
                content=chunk.text,
                content_hash=stable_content_hash(chunk.text),
                token_count=len(_TOKEN_PATTERN.findall(chunk.text)),
                ordinal=ordinal,
            )
            for ordinal, chunk in enumerate(policy_chunks)
        ]
        return await self._repository.create_version(
            tenant_id,
            DocumentDraft(
                document_key=document_key,
                title=title,
                source_uri=source_uri,
                content_hash=stable_content_hash(markdown),
            ),
            chunks,
            self._model_key,
        )


class EmbeddingWorker:
    def __init__(
        self,
        repository: WorkerRepository,
        provider: EmbeddingProvider,
        *,
        clock: Callable[[], datetime] | None = None,
        retry_delay: timedelta = timedelta(minutes=5),
    ) -> None:
        if retry_delay <= timedelta(0):
            raise ValueError("retry_delay must be positive")
        self._repository = repository
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(UTC))
        self._retry_delay = retry_delay
        self._claimed: dict[UUID, ClaimedEmbeddingJob] = {}

    async def claim(self, tenant_id: UUID, limit: int) -> list[ClaimedEmbeddingJob]:
        now = _aware_clock_value(self._clock())
        jobs = await self._repository.claim(
            tenant_id,
            limit,
            model_key=self._provider.model_key,
            now=now,
        )
        for job in jobs:
            if job.tenant_id != tenant_id:
                raise RuntimeError("repository returned a job for the wrong tenant")
            if job.model_key != self._provider.model_key:
                raise RuntimeError("repository returned a job for the wrong embedding model")
            existing = self._claimed.get(job.id)
            if existing is not None and existing != job:
                raise RuntimeError("repository returned a conflicting job identity")
            self._claimed[job.id] = job
        return jobs

    async def succeed(self, job_id: UUID, vector: Sequence[object]) -> bool:
        job = self._require_claim(job_id)
        succeeded = await self._repository.succeed(
            job.tenant_id,
            job.id,
            vector,
            expected_content_hash=job.content_hash,
            now=_aware_clock_value(self._clock()),
        )
        self._claimed.pop(job.id, None)
        return succeeded

    async def retry(self, job_id: UUID, code: str, next_retry_at: datetime) -> bool:
        job = self._require_claim(job_id)
        retried = await self._repository.retry(
            job.tenant_id,
            job.id,
            code=code,
            next_retry_at=next_retry_at,
            now=_aware_clock_value(self._clock()),
        )
        self._claimed.pop(job.id, None)
        return retried

    async def fail(self, job_id: UUID, code: str) -> bool:
        job = self._require_claim(job_id)
        failed = await self._repository.fail(
            job.tenant_id,
            job.id,
            code=code,
            now=_aware_clock_value(self._clock()),
        )
        self._claimed.pop(job.id, None)
        return failed

    async def run_once(self, tenant_id: UUID, limit: int) -> WorkerRunResult:
        jobs = await self.claim(tenant_id, limit)
        succeeded = 0
        retried = 0
        failed = 0
        for job in jobs:
            try:
                vector = (await self._provider.embed([job.content]))[0]
            except EmbeddingError as error:
                if error.retryable:
                    retry_at = _aware_clock_value(self._clock()) + self._retry_delay
                    if await self.retry(job.id, error.code, retry_at):
                        retried += 1
                elif await self.fail(job.id, error.code):
                    failed += 1
                continue
            except Exception:
                retry_at = _aware_clock_value(self._clock()) + self._retry_delay
                if await self.retry(
                    job.id,
                    "EMBEDDING_PROVIDER_UNAVAILABLE",
                    retry_at,
                ):
                    retried += 1
                continue
            if await self.succeed(job.id, vector):
                succeeded += 1
        return WorkerRunResult(
            claimed=len(jobs),
            succeeded=succeeded,
            retried=retried,
            failed=failed,
            completed_at=_aware_clock_value(self._clock()),
        )

    def _require_claim(self, job_id: UUID) -> ClaimedEmbeddingJob:
        if not isinstance(job_id, UUID):
            raise TypeError("job_id must be a UUID")
        job = self._claimed.get(job_id)
        if job is None:
            raise ValueError("job was not claimed by this worker")
        return job


def stable_content_hash(content: str) -> str:
    if not isinstance(content, str):
        raise TypeError("content must be text")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _validate_document_input(
    tenant_id: UUID,
    document_key: str,
    title: str,
    markdown: str,
    source_uri: str,
) -> None:
    if not isinstance(tenant_id, UUID):
        raise TypeError("tenant_id must be a UUID")
    if not document_key or document_key.strip() != document_key or len(document_key) > 160:
        raise ValueError("document_key must be nonblank and at most 160 characters")
    if not title or title.strip() != title or len(title) > 300:
        raise ValueError("title must be nonblank and at most 300 characters")
    if not markdown.strip():
        raise ValueError("markdown must be nonblank")
    if not source_uri or source_uri.strip() != source_uri or len(source_uri) > 2048:
        raise ValueError("source_uri must be nonblank and at most 2048 characters")
    if any("\x00" in value for value in (document_key, title, markdown, source_uri)):
        raise ValueError("document fields must not contain NUL characters")


def _aware_clock_value(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value
