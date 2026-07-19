from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Literal, Protocol, cast
from uuid import UUID

from sqlalchemy import text

from app.db import Database
from app.knowledge.embedding.base import (
    EmbeddingCapabilityError,
    EmbeddingProvider,
    normalize_embeddings,
)
from app.knowledge.models import (
    ActiveKnowledgeChunk,
    RetrievalHit,
    RetrievalResult,
)

HYBRID_RETRIEVAL_DEGRADED = "HYBRID_RETRIEVAL_DEGRADED"
_RETRIEVAL_CANDIDATE_LIMIT = 50
_RRF_K = 60


class BM25Search(Protocol):
    async def search(
        self,
        tenant_id: UUID,
        query: str,
        limit: int = _RETRIEVAL_CANDIDATE_LIMIT,
    ) -> list[ActiveKnowledgeChunk]: ...


class VectorSearch(Protocol):
    async def search(
        self,
        tenant_id: UUID,
        query_vector: Sequence[object],
        model_key: str,
        limit: int = _RETRIEVAL_CANDIDATE_LIMIT,
    ) -> list[ActiveKnowledgeChunk]: ...


class PostgresActiveChunkSource:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def load_active_chunks(self, tenant_id: UUID) -> list[ActiveKnowledgeChunk]:
        _require_tenant(tenant_id)
        async with self._database.session(tenant_id) as session:
            result = await session.execute(
                text(
                    """
                    SELECT c.id::text AS chunk_id,
                           d.document_key AS document_id,
                           d.document_key,
                           d.title,
                           c.section,
                           c.content AS text,
                           c.ordinal,
                           d.version AS document_version,
                           c.content_hash
                    FROM knowledge_document AS d
                    JOIN knowledge_chunk AS c
                      ON c.tenant_id = d.tenant_id
                     AND c.document_id = d.id
                    WHERE d.tenant_id = :tenant_id
                      AND d.status = 'ACTIVE'
                      AND c.status = 'ACTIVE'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM knowledge_document AS newer
                          WHERE newer.tenant_id = d.tenant_id
                            AND newer.document_key = d.document_key
                            AND newer.status = 'ACTIVE'
                            AND newer.version > d.version
                      )
                    ORDER BY d.document_key, c.ordinal, c.id
                    """
                ),
                {"tenant_id": tenant_id},
            )
            return [ActiveKnowledgeChunk.model_validate(row) for row in result.mappings().all()]


class PostgresVectorPolicyIndex:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def search(
        self,
        tenant_id: UUID,
        query_vector: Sequence[object],
        model_key: str,
        limit: int = _RETRIEVAL_CANDIDATE_LIMIT,
    ) -> list[ActiveKnowledgeChunk]:
        _require_tenant(tenant_id)
        _require_route_limit(limit)
        if not model_key or model_key.strip() != model_key or "\x00" in model_key:
            raise ValueError("model_key must be nonblank")
        vector = normalize_embeddings([query_vector], expected_count=1)[0]
        async with self._database.session(tenant_id) as session:
            await session.execute(
                text("SELECT set_config('hnsw.ef_search', '100', true)")
            )
            result = await session.execute(
                text(
                    """
                    SELECT c.id::text AS chunk_id,
                           d.document_key AS document_id,
                           d.document_key,
                           d.title,
                           c.section,
                           c.content AS text,
                           c.ordinal,
                           d.version AS document_version,
                           c.content_hash
                    FROM knowledge_embedding AS e
                    JOIN knowledge_chunk AS c
                      ON c.tenant_id = e.tenant_id
                     AND c.id = e.chunk_id
                    JOIN knowledge_document AS d
                      ON d.tenant_id = c.tenant_id
                     AND d.id = c.document_id
                    WHERE e.tenant_id = :tenant_id
                      AND e.model_key = :model_key
                      AND e.content_hash = c.content_hash
                      AND d.status = 'ACTIVE'
                      AND c.status = 'ACTIVE'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM knowledge_document AS newer
                          WHERE newer.tenant_id = d.tenant_id
                            AND newer.document_key = d.document_key
                            AND newer.status = 'ACTIVE'
                            AND newer.version > d.version
                      )
                    ORDER BY e.embedding <=> CAST(:query_vector AS vector),
                             d.document_key, c.ordinal, c.id
                    LIMIT :limit
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "model_key": model_key,
                    "query_vector": json.dumps(vector, separators=(",", ":")),
                    "limit": limit,
                },
            )
            return [ActiveKnowledgeChunk.model_validate(row) for row in result.mappings().all()]


class HybridPolicyIndex:
    def __init__(
        self,
        *,
        bm25: BM25Search,
        vector: VectorSearch,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._bm25 = bm25
        self._vector = vector
        self._embedding_provider = embedding_provider

    async def search(
        self,
        tenant_id: UUID,
        query: str,
        limit: int = 5,
    ) -> RetrievalResult:
        _require_tenant(tenant_id)
        _require_public_limit(limit)
        normalized_query = query.strip()
        if not normalized_query:
            return RetrievalResult(hits=(), mode="none")

        bm25_outcome, vector_outcome = await asyncio.gather(
            self._bm25.search(
                tenant_id,
                normalized_query,
                limit=_RETRIEVAL_CANDIDATE_LIMIT,
            ),
            self._search_vector(tenant_id, normalized_query),
            return_exceptions=True,
        )
        _raise_control_flow(bm25_outcome)
        _raise_control_flow(vector_outcome)

        bm25_ok = not isinstance(bm25_outcome, Exception)
        vector_disabled = isinstance(vector_outcome, EmbeddingCapabilityError)
        vector_ok = not isinstance(vector_outcome, Exception)
        bm25_hits = (
            cast(list[ActiveKnowledgeChunk], bm25_outcome) if bm25_ok else []
        )
        vector_hits = (
            cast(list[ActiveKnowledgeChunk], vector_outcome) if vector_ok else []
        )

        warnings: tuple[str, ...] = ()
        if not bm25_ok or (not vector_ok and not vector_disabled):
            warnings = (HYBRID_RETRIEVAL_DEGRADED,)

        mode: Literal["hybrid", "bm25", "vector", "none"]
        if bm25_ok and vector_ok:
            mode = "hybrid"
        elif bm25_ok:
            mode = "bm25"
        elif vector_ok:
            mode = "vector"
        else:
            mode = "none"
        return RetrievalResult(
            hits=tuple(_rrf_fuse(bm25_hits, vector_hits, limit=limit)),
            mode=mode,
            warnings=warnings,
        )

    async def _search_vector(
        self,
        tenant_id: UUID,
        query: str,
    ) -> list[ActiveKnowledgeChunk]:
        vectors = await self._embedding_provider.embed([query])
        if len(vectors) != 1:
            raise RuntimeError("embedding provider returned an invalid result count")
        return await self._vector.search(
            tenant_id,
            vectors[0],
            self._embedding_provider.model_key,
            limit=_RETRIEVAL_CANDIDATE_LIMIT,
        )


def _rrf_fuse(
    bm25_hits: Sequence[ActiveKnowledgeChunk],
    vector_hits: Sequence[ActiveKnowledgeChunk],
    *,
    limit: int,
) -> list[RetrievalHit]:
    candidates: dict[str, ActiveKnowledgeChunk] = {}
    bm25_ranks: dict[str, int] = {}
    vector_ranks: dict[str, int] = {}
    for rank, candidate in enumerate(bm25_hits, start=1):
        if candidate.chunk_id not in bm25_ranks:
            bm25_ranks[candidate.chunk_id] = rank
            candidates[candidate.chunk_id] = candidate
    for rank, candidate in enumerate(vector_hits, start=1):
        if candidate.chunk_id not in vector_ranks:
            vector_ranks[candidate.chunk_id] = rank
            candidates.setdefault(candidate.chunk_id, candidate)

    hits = [
        RetrievalHit(
            **candidate.model_dump(),
            bm25_rank=bm25_ranks.get(chunk_id),
            vector_rank=vector_ranks.get(chunk_id),
            rrf_score=(
                (1 / (_RRF_K + bm25_ranks[chunk_id]))
                if chunk_id in bm25_ranks
                else 0.0
            )
            + (
                (1 / (_RRF_K + vector_ranks[chunk_id]))
                if chunk_id in vector_ranks
                else 0.0
            ),
        )
        for chunk_id, candidate in candidates.items()
    ]
    hits.sort(
        key=lambda hit: (
            -hit.rrf_score,
            hit.document_key,
            hit.ordinal,
            hit.chunk_id,
        )
    )
    return hits[:limit]


def _require_tenant(tenant_id: UUID) -> None:
    if not isinstance(tenant_id, UUID):
        raise TypeError("tenant_id must be a UUID")


def _require_route_limit(limit: int) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= _RETRIEVAL_CANDIDATE_LIMIT:
        raise ValueError("route limit must be between 1 and 50")


def _require_public_limit(limit: int) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= _RETRIEVAL_CANDIDATE_LIMIT:
        raise ValueError("limit must be between 1 and 50")


def _raise_control_flow(outcome: object) -> None:
    if isinstance(outcome, BaseException) and not isinstance(outcome, Exception):
        raise outcome
