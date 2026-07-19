from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import jieba
from rank_bm25 import BM25Plus

from app.knowledge.models import (
    ActiveKnowledgeChunk,
    PolicyChunk,
    RetrievalHit,
    RetrievalResult,
    SearchHit,
)


def tokenize(text: str) -> list[str]:
    return [token.strip().lower() for token in jieba.lcut(text) if token.strip()]


class BM25PolicyIndex:
    def __init__(self, chunks: Sequence[PolicyChunk]) -> None:
        self._chunks = list(chunks)
        corpus = [tokenize(f"{chunk.title} {chunk.section} {chunk.text}") for chunk in chunks]
        self._index = BM25Plus(corpus) if corpus else None

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        if self._index is None or not query.strip() or limit <= 0:
            return []
        scores = self._index.get_scores(tokenize(query))
        ranked = sorted(
            (
                (float(score), chunk)
                for score, chunk in zip(scores, self._chunks, strict=True)
                if float(score) > 0
            ),
            key=lambda item: (-item[0], item[1].chunk_id),
        )
        return [SearchHit(**chunk.model_dump(), score=score) for score, chunk in ranked[:limit]]


class StaticTenantBM25PolicyIndex:
    """Tenant-context adapter for checked-in baseline policies during hybrid startup."""

    def __init__(self, chunks: Sequence[PolicyChunk]) -> None:
        self._index = BM25PolicyIndex(chunks)

    async def search(
        self,
        tenant_id: UUID,
        query: str,
        limit: int = 5,
    ) -> RetrievalResult:
        if not isinstance(tenant_id, UUID):
            raise TypeError("tenant_id must be a UUID")
        hits = self._index.search(query, limit=limit)
        retrieval_hits = tuple(
            RetrievalHit(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                document_key=hit.document_id,
                title=hit.title,
                section=hit.section,
                text=hit.text,
                ordinal=rank - 1,
                document_version=0,
                content_hash=hashlib.sha256(hit.text.encode("utf-8")).hexdigest(),
                bm25_rank=rank,
                vector_rank=None,
                rrf_score=1 / (60 + rank),
            )
            for rank, hit in enumerate(hits, start=1)
        )
        return RetrievalResult(hits=retrieval_hits, mode="bm25")


class ActiveChunkSource(Protocol):
    async def load_active_chunks(self, tenant_id: UUID) -> list[ActiveKnowledgeChunk]: ...


@dataclass(frozen=True)
class _TenantCacheEntry:
    signature: tuple[tuple[str, int, str], ...]
    chunks: tuple[ActiveKnowledgeChunk, ...]
    index: BM25Plus | None


class TenantBM25PolicyIndex:
    """Cache tokenization by tenant while checking active persisted versions."""

    def __init__(self, source: ActiveChunkSource, *, max_tenants: int = 128) -> None:
        if isinstance(max_tenants, bool) or max_tenants <= 0:
            raise ValueError("max_tenants must be positive")
        self._source = source
        self._max_tenants = max_tenants
        self._cache: OrderedDict[UUID, _TenantCacheEntry] = OrderedDict()

    async def search(
        self,
        tenant_id: UUID,
        query: str,
        limit: int = 50,
    ) -> list[ActiveKnowledgeChunk]:
        if not isinstance(tenant_id, UUID):
            raise TypeError("tenant_id must be a UUID")
        if not query.strip() or limit <= 0:
            return []
        active_chunks = await self._source.load_active_chunks(tenant_id)
        signature = tuple(
            (chunk.chunk_id, chunk.document_version, chunk.content_hash) for chunk in active_chunks
        )
        cached = self._cache.get(tenant_id)
        if cached is None or cached.signature != signature:
            cached = self._build_entry(signature, active_chunks)
            self._cache[tenant_id] = cached
        self._cache.move_to_end(tenant_id)
        while len(self._cache) > self._max_tenants:
            self._cache.popitem(last=False)
        if cached.index is None:
            return []
        scores = cached.index.get_scores(tokenize(query))
        ranked = sorted(
            (
                (float(score), chunk)
                for score, chunk in zip(scores, cached.chunks, strict=True)
                if float(score) > 0
            ),
            key=lambda item: (
                -item[0],
                item[1].document_key,
                item[1].ordinal,
                item[1].chunk_id,
            ),
        )
        return [chunk for _, chunk in ranked[:limit]]

    def invalidate(self, tenant_id: UUID) -> None:
        self._cache.pop(tenant_id, None)

    @staticmethod
    def _build_entry(
        signature: tuple[tuple[str, int, str], ...],
        chunks: Sequence[ActiveKnowledgeChunk],
    ) -> _TenantCacheEntry:
        frozen_chunks = tuple(chunks)
        corpus = [
            tokenize(f"{chunk.title} {chunk.section} {chunk.text}") for chunk in frozen_chunks
        ]
        return _TenantCacheEntry(
            signature=signature,
            chunks=frozen_chunks,
            index=BM25Plus(corpus) if corpus else None,
        )
