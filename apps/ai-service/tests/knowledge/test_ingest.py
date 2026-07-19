from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.knowledge.embedding.base import (
    EmbeddingProviderUnavailableError,
    EmbeddingValidationError,
)
from app.knowledge.embedding.deterministic import DeterministicEmbeddingProvider
from app.knowledge.ingest import EmbeddingWorker, KnowledgeIngestor, stable_content_hash
from app.knowledge.models import (
    ChunkDraft,
    ClaimedEmbeddingJob,
    DocumentDraft,
    IngestResult,
)

TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")
NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


class RecordingIngestRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, DocumentDraft, list[ChunkDraft], str]] = []
        self.versions: dict[tuple[UUID, str], list[tuple[str, UUID]]] = {}

    async def create_version(
        self,
        tenant_id: UUID,
        document: DocumentDraft,
        chunks: list[ChunkDraft],
        model_key: str,
    ) -> IngestResult:
        self.calls.append((tenant_id, document, chunks, model_key))
        versions = self.versions.setdefault((tenant_id, document.document_key), [])
        if versions and versions[-1][0] == document.content_hash:
            return IngestResult(
                document_id=versions[-1][1],
                version=len(versions),
                chunk_count=0,
                skipped=True,
            )
        document_id = uuid4()
        versions.append((document.content_hash, document_id))
        return IngestResult(
            document_id=document_id,
            version=len(versions),
            chunk_count=len(chunks),
            skipped=False,
        )


class RecordingWorkerRepository:
    def __init__(self, jobs: list[ClaimedEmbeddingJob]) -> None:
        self.jobs = jobs
        self.claim_calls: list[tuple[UUID, str, int, datetime]] = []
        self.successes: list[tuple[UUID, UUID, str, list[float], datetime]] = []
        self.retries: list[tuple[UUID, UUID, str, datetime, datetime]] = []
        self.failures: list[tuple[UUID, UUID, str, datetime]] = []

    async def claim(
        self,
        tenant_id: UUID,
        limit: int,
        *,
        model_key: str,
        now: datetime,
    ) -> list[ClaimedEmbeddingJob]:
        self.claim_calls.append((tenant_id, model_key, limit, now))
        return self.jobs[:limit]

    async def succeed(
        self,
        tenant_id: UUID,
        job_id: UUID,
        vector: list[float],
        *,
        expected_content_hash: str,
        now: datetime,
    ) -> bool:
        self.successes.append((tenant_id, job_id, expected_content_hash, vector, now))
        return True

    async def retry(
        self,
        tenant_id: UUID,
        job_id: UUID,
        *,
        code: str,
        next_retry_at: datetime,
        now: datetime,
    ) -> bool:
        self.retries.append((tenant_id, job_id, code, next_retry_at, now))
        return True

    async def fail(
        self,
        tenant_id: UUID,
        job_id: UUID,
        *,
        code: str,
        now: datetime,
    ) -> bool:
        self.failures.append((tenant_id, job_id, code, now))
        return True


class FailingEmbeddingProvider:
    model_key = "synthetic-failing-model"
    dimensions = 512
    loaded = False

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingProviderUnavailableError


class InvalidVectorEmbeddingProvider:
    model_key = "synthetic-invalid-model"
    dimensions = 512
    loaded = True

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingValidationError


def claimed_job(content: str = "返工规则") -> ClaimedEmbeddingJob:
    return ClaimedEmbeddingJob(
        id=uuid4(),
        tenant_id=TENANT_ID,
        document_id=uuid4(),
        chunk_id=uuid4(),
        model_key="deterministic-shake256-v1",
        content=content,
        content_hash="a" * 64,
        retry_count=0,
    )


def test_stable_content_hash_uses_sha256_utf8() -> None:
    assert stable_content_hash("返工规则") == (
        "7a7e905af43863461ba0f00133a334cc15d397cf77d8c2301c4cbe128d7a8660"
    )
    assert stable_content_hash("返工规则") == stable_content_hash("返工规则")
    assert stable_content_hash("返工规则\n") != stable_content_hash("返工规则")


@pytest.mark.parametrize("model_key", ["", " padded", "bad\x00model"])
def test_ingestor_rejects_invalid_model_keys(model_key: str) -> None:
    with pytest.raises(ValueError):
        KnowledgeIngestor(RecordingIngestRepository(), model_key=model_key)


@pytest.mark.asyncio
async def test_ingest_uses_existing_markdown_chunk_rules_and_unique_chunk_keys() -> None:
    repository = RecordingIngestRepository()
    ingestor = KnowledgeIngestor(repository, model_key="synthetic-model")
    markdown = "# 返工规则\n## 适用范围\n" + ("甲" * 520) + "。\n### 时限\n两日内完成。"

    result = await ingestor.ingest(
        TENANT_ID,
        "rework-policy",
        "返工规则",
        markdown,
        "policy://rework",
    )

    assert result.skipped is False
    _, document, chunks, model_key = repository.calls[0]
    assert document.content_hash == stable_content_hash(markdown)
    assert model_key == "synthetic-model"
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert len({chunk.chunk_key for chunk in chunks}) == len(chunks)
    assert all(len(chunk.content) <= 500 for chunk in chunks)
    assert {chunk.section for chunk in chunks} == {"适用范围", "时限"}
    assert all(chunk.content_hash == stable_content_hash(chunk.content) for chunk in chunks)


@pytest.mark.asyncio
async def test_duplicate_ingest_skips_and_changed_content_creates_next_version() -> None:
    repository = RecordingIngestRepository()
    ingestor = KnowledgeIngestor(repository, model_key="synthetic-model")
    arguments = (TENANT_ID, "rework-policy", "返工规则", "## 规则\n必须整改。", "policy://x")

    first = await ingestor.ingest(*arguments)
    duplicate = await ingestor.ingest(*arguments)
    changed = await ingestor.ingest(
        TENANT_ID,
        "rework-policy",
        "返工规则",
        "## 规则\n必须在两日内整改。",
        "policy://x",
    )

    assert (first.version, first.skipped) == (1, False)
    assert (duplicate.version, duplicate.skipped) == (1, True)
    assert (changed.version, changed.skipped) == (2, False)


@pytest.mark.parametrize(
    ("document_key", "title", "markdown", "source_uri"),
    [
        ("", "标题", "## 规则\n内容", "policy://x"),
        ("key", "", "## 规则\n内容", "policy://x"),
        ("key", "标题", "", "policy://x"),
        ("key", "标题", "没有二级标题", "policy://x"),
        ("key", "标题", "## 规则\n内容", ""),
        ("key\x00", "标题", "## 规则\n内容", "policy://x"),
        ("key", "标题", "## 规则\n包含\x00字符", "policy://x"),
        ("key", "标题", "## " + ("章" * 501) + "\n内容", "policy://x"),
    ],
)
@pytest.mark.asyncio
async def test_ingest_rejects_invalid_or_unchunkable_documents(
    document_key: str,
    title: str,
    markdown: str,
    source_uri: str,
) -> None:
    ingestor = KnowledgeIngestor(RecordingIngestRepository(), model_key="synthetic-model")
    with pytest.raises(ValueError):
        await ingestor.ingest(
            TENANT_ID,
            document_key,
            title,
            markdown,
            source_uri,
        )


@pytest.mark.asyncio
async def test_worker_claim_and_succeed_use_claimed_tenant_context() -> None:
    job = claimed_job()
    repository = RecordingWorkerRepository([job])
    worker = EmbeddingWorker(
        repository,
        DeterministicEmbeddingProvider(),
        clock=lambda: NOW,
    )

    assert await worker.claim(TENANT_ID, 10) == [job]
    vector = [1.0] + [0.0] * 511
    assert await worker.succeed(job.id, vector)

    assert repository.claim_calls == [
        (TENANT_ID, "deterministic-shake256-v1", 10, NOW)
    ]
    assert repository.successes == [(TENANT_ID, job.id, "a" * 64, vector, NOW)]


@pytest.mark.asyncio
async def test_worker_rejects_claimed_job_for_a_different_embedding_model() -> None:
    wrong_model_job = claimed_job().model_copy(update={"model_key": "other-model"})
    worker = EmbeddingWorker(
        RecordingWorkerRepository([wrong_model_job]),
        DeterministicEmbeddingProvider(),
        clock=lambda: NOW,
    )

    with pytest.raises(RuntimeError, match="wrong embedding model"):
        await worker.claim(TENANT_ID, 1)


@pytest.mark.asyncio
async def test_worker_run_once_embeds_claimed_content_outside_repository_transaction() -> None:
    jobs = [claimed_job("规则一"), claimed_job("规则二")]
    repository = RecordingWorkerRepository(jobs)
    worker = EmbeddingWorker(
        repository,
        DeterministicEmbeddingProvider(),
        clock=lambda: NOW,
    )

    result = await worker.run_once(TENANT_ID, limit=10)

    assert result.claimed == 2
    assert result.succeeded == 2
    assert result.retried == 0
    assert result.failed == 0
    assert [success[1] for success in repository.successes] == [job.id for job in jobs]
    assert all(len(success[3]) == 512 for success in repository.successes)


@pytest.mark.asyncio
async def test_worker_provider_failure_moves_job_to_retry_wait_without_activation() -> None:
    job = claimed_job().model_copy(update={"model_key": "synthetic-failing-model"})
    repository = RecordingWorkerRepository([job])
    worker = EmbeddingWorker(
        repository,
        FailingEmbeddingProvider(),
        clock=lambda: NOW,
        retry_delay=timedelta(minutes=5),
    )

    result = await worker.run_once(TENANT_ID, limit=1)

    assert result.claimed == 1
    assert result.succeeded == 0
    assert result.retried == 1
    assert result.failed == 0
    assert repository.successes == []
    assert repository.retries == [
        (
            TENANT_ID,
            job.id,
            "EMBEDDING_PROVIDER_UNAVAILABLE",
            NOW + timedelta(minutes=5),
            NOW,
        )
    ]


@pytest.mark.asyncio
async def test_worker_nonretryable_embedding_error_marks_job_failed() -> None:
    job = claimed_job().model_copy(update={"model_key": "synthetic-invalid-model"})
    repository = RecordingWorkerRepository([job])
    worker = EmbeddingWorker(
        repository,
        InvalidVectorEmbeddingProvider(),
        clock=lambda: NOW,
    )

    result = await worker.run_once(TENANT_ID, limit=1)

    assert result.claimed == 1
    assert result.succeeded == 0
    assert result.retried == 0
    assert result.failed == 1
    assert repository.retries == []
    assert repository.failures == [
        (TENANT_ID, job.id, "EMBEDDING_VECTOR_INVALID", NOW)
    ]
