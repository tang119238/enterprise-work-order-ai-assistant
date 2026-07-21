from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PolicyChunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    document_id: str
    title: str
    section: str
    text: str


class SearchHit(PolicyChunk):
    score: float


class DocumentDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_key: str
    title: str
    source_uri: str
    content_hash: str


class ChunkDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_key: str
    section: str
    content: str
    content_hash: str
    token_count: int
    ordinal: int


class IngestResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: UUID
    version: int
    chunk_count: int
    skipped: bool


class ClaimedEmbeddingJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    document_id: UUID
    chunk_id: UUID
    model_key: str
    content: str
    content_hash: str
    retry_count: int


class WorkerRunResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    claimed: int
    succeeded: int
    retried: int
    failed: int
    completed_at: datetime


class ActiveKnowledgeChunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    document_id: str
    document_key: str
    title: str
    section: str
    text: str
    ordinal: int
    document_version: int
    content_hash: str


class RetrievalHit(ActiveKnowledgeChunk):
    bm25_rank: int | None = None
    vector_rank: int | None = None
    rrf_score: float


class RetrievalResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    hits: tuple[RetrievalHit, ...]
    mode: Literal["hybrid", "bm25", "vector", "none"]
    warnings: tuple[str, ...] = ()
