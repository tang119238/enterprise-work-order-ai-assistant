from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, Self
from uuid import UUID

import pytest

from app.knowledge.bm25 import TenantBM25PolicyIndex
from app.knowledge.embedding.base import EmbeddingProviderUnavailableError
from app.knowledge.embedding.registry import DisabledEmbeddingProvider
from app.knowledge.hybrid import (
    HYBRID_RETRIEVAL_DEGRADED,
    HybridPolicyIndex,
    PostgresActiveChunkSource,
    PostgresVectorPolicyIndex,
)
from app.knowledge.models import ActiveKnowledgeChunk

TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = UUID("22222222-2222-2222-2222-222222222222")


def chunk(
    chunk_id: str,
    document_key: str,
    ordinal: int,
    *,
    text: str | None = None,
    version: int = 1,
    content_hash: str | None = None,
) -> ActiveKnowledgeChunk:
    return ActiveKnowledgeChunk(
        chunk_id=chunk_id,
        document_id=document_key,
        document_key=document_key,
        title=f"{document_key} title",
        section="Rules",
        text=text or f"{document_key} content",
        ordinal=ordinal,
        document_version=version,
        content_hash=content_hash or (chunk_id[0].lower() * 64),
    )


class StubBM25:
    def __init__(self, hits: Sequence[ActiveKnowledgeChunk] = (), error: Exception | None = None):
        self.hits = list(hits)
        self.error = error
        self.calls: list[tuple[UUID, str, int]] = []

    async def search(
        self,
        tenant_id: UUID,
        query: str,
        limit: int = 50,
    ) -> list[ActiveKnowledgeChunk]:
        self.calls.append((tenant_id, query, limit))
        if self.error is not None:
            raise self.error
        return self.hits[:limit]


class StubVector:
    def __init__(self, hits: Sequence[ActiveKnowledgeChunk] = (), error: Exception | None = None):
        self.hits = list(hits)
        self.error = error
        self.calls: list[tuple[UUID, list[float], str, int]] = []

    async def search(
        self,
        tenant_id: UUID,
        query_vector: Sequence[object],
        model_key: str,
        limit: int = 50,
    ) -> list[ActiveKnowledgeChunk]:
        self.calls.append((tenant_id, list(query_vector), model_key, limit))
        if self.error is not None:
            raise self.error
        return self.hits[:limit]


class StubEmbeddingProvider:
    model_key = "synthetic-model/512"
    dimensions = 512
    loaded = True

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.error is not None:
            raise self.error
        return [[1.0] + [0.0] * 511 for _ in texts]


@pytest.mark.asyncio
async def test_rrf_merges_both_routes_with_exact_one_based_contributions() -> None:
    shared = chunk("a-shared", "a-shared-document", 0)
    vector_leader = chunk("v-leading", "b-vector-document", 0)
    bm25_only = chunk("b-only", "c-bm25-document", 0)
    bm25 = StubBM25([shared, bm25_only])
    vector = StubVector([vector_leader, shared])
    provider = StubEmbeddingProvider()
    index = HybridPolicyIndex(bm25=bm25, vector=vector, embedding_provider=provider)

    result = await index.search(TENANT_A, "返工", limit=5)

    assert [hit.chunk_id for hit in result.hits] == ["a-shared", "v-leading", "b-only"]
    assert result.hits[0].bm25_rank == 1
    assert result.hits[0].vector_rank == 2
    assert result.hits[0].rrf_score == pytest.approx(1 / 61 + 1 / 62)
    assert result.hits[1].rrf_score == pytest.approx(1 / 61)
    assert result.hits[2].rrf_score == pytest.approx(1 / 62)
    assert result.mode == "hybrid"
    assert result.warnings == ()
    assert bm25.calls == [(TENANT_A, "返工", 50)]
    assert provider.calls == [["返工"]]
    assert vector.calls[0][0] == TENANT_A
    assert vector.calls[0][2:] == ("synthetic-model/512", 50)


@pytest.mark.asyncio
async def test_rrf_ties_sort_by_document_key_then_ordinal() -> None:
    bm25 = StubBM25(
        [
            chunk("z", "same-document", 2),
            chunk("b", "b-document", 0),
            chunk("a", "a-document", 0),
        ]
    )
    vector = StubVector(
        [
            chunk("z", "same-document", 2),
            chunk("a", "a-document", 0),
            chunk("b", "b-document", 0),
        ]
    )
    index = HybridPolicyIndex(
        bm25=bm25,
        vector=vector,
        embedding_provider=StubEmbeddingProvider(),
    )

    result = await index.search(TENANT_A, "规则")

    assert [hit.chunk_id for hit in result.hits] == ["z", "a", "b"]
    assert result.hits[1].rrf_score == pytest.approx(result.hits[2].rrf_score)


@pytest.mark.asyncio
async def test_vector_only_hits_and_successful_empty_routes_remain_hybrid() -> None:
    vector_hit = chunk("v-only", "vector-document", 0)
    vector_only = HybridPolicyIndex(
        bm25=StubBM25(),
        vector=StubVector([vector_hit]),
        embedding_provider=StubEmbeddingProvider(),
    )
    empty = HybridPolicyIndex(
        bm25=StubBM25(),
        vector=StubVector(),
        embedding_provider=StubEmbeddingProvider(),
    )

    vector_result = await vector_only.search(TENANT_A, "同义改写")
    empty_result = await empty.search(TENANT_A, "硬负例")

    assert [hit.chunk_id for hit in vector_result.hits] == ["v-only"]
    assert vector_result.hits[0].bm25_rank is None
    assert vector_result.hits[0].vector_rank == 1
    assert vector_result.mode == "hybrid"
    assert empty_result.hits == ()
    assert empty_result.mode == "hybrid"
    assert empty_result.warnings == ()


@pytest.mark.asyncio
async def test_vector_failure_returns_bm25_with_explicit_degradation() -> None:
    bm25_hit = chunk("b-only", "bm25-document", 0)
    index = HybridPolicyIndex(
        bm25=StubBM25([bm25_hit]),
        vector=StubVector(error=RuntimeError("database detail must not leak")),
        embedding_provider=StubEmbeddingProvider(),
    )

    result = await index.search(TENANT_A, "规则")

    assert [hit.chunk_id for hit in result.hits] == ["b-only"]
    assert result.mode == "bm25"
    assert result.warnings == (HYBRID_RETRIEVAL_DEGRADED,)
    assert "database detail" not in repr(result)


@pytest.mark.asyncio
async def test_embedding_failure_degrades_but_disabled_vector_is_deliberate_bm25() -> None:
    bm25_hit = chunk("b-only", "bm25-document", 0)
    failing = HybridPolicyIndex(
        bm25=StubBM25([bm25_hit]),
        vector=StubVector(),
        embedding_provider=StubEmbeddingProvider(EmbeddingProviderUnavailableError()),
    )
    disabled_vector = StubVector()
    disabled = HybridPolicyIndex(
        bm25=StubBM25([bm25_hit]),
        vector=disabled_vector,
        embedding_provider=DisabledEmbeddingProvider(),
    )

    failing_result = await failing.search(TENANT_A, "规则")
    disabled_result = await disabled.search(TENANT_A, "规则")

    assert failing_result.mode == "bm25"
    assert failing_result.warnings == (HYBRID_RETRIEVAL_DEGRADED,)
    assert disabled_result.mode == "bm25"
    assert disabled_result.warnings == ()
    assert disabled_vector.calls == []


@pytest.mark.asyncio
async def test_bm25_failure_returns_vector_and_marks_degradation() -> None:
    vector_hit = chunk("v-only", "vector-document", 0)
    index = HybridPolicyIndex(
        bm25=StubBM25(error=RuntimeError("bm25 detail must not leak")),
        vector=StubVector([vector_hit]),
        embedding_provider=StubEmbeddingProvider(),
    )

    result = await index.search(TENANT_A, "规则")

    assert [hit.chunk_id for hit in result.hits] == ["v-only"]
    assert result.mode == "vector"
    assert result.warnings == (HYBRID_RETRIEVAL_DEGRADED,)


@pytest.mark.asyncio
async def test_empty_or_invalid_search_boundaries_make_no_route_calls() -> None:
    bm25 = StubBM25()
    vector = StubVector()
    provider = StubEmbeddingProvider()
    index = HybridPolicyIndex(bm25=bm25, vector=vector, embedding_provider=provider)

    empty = await index.search(TENANT_A, "   ")

    assert empty.hits == ()
    assert empty.mode == "none"
    assert bm25.calls == []
    assert vector.calls == []
    assert provider.calls == []
    with pytest.raises(TypeError):
        await index.search("not-a-uuid", "规则")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await index.search(TENANT_A, "规则", limit=0)
    with pytest.raises(ValueError):
        await index.search(TENANT_A, "规则", limit=51)


class MutableChunkSource:
    def __init__(self, chunks_by_tenant: dict[UUID, list[ActiveKnowledgeChunk]]) -> None:
        self.chunks_by_tenant = chunks_by_tenant
        self.calls: list[UUID] = []

    async def load_active_chunks(self, tenant_id: UUID) -> list[ActiveKnowledgeChunk]:
        self.calls.append(tenant_id)
        return self.chunks_by_tenant.get(tenant_id, [])


@pytest.mark.asyncio
async def test_tenant_bm25_cache_is_tenant_scoped_and_rebuilds_on_version_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builds: list[list[list[str]]] = []

    class CountingBM25:
        def __init__(self, corpus: list[list[str]]) -> None:
            builds.append(corpus)

        def get_scores(self, _: list[str]) -> list[float]:
            return [1.0]

    monkeypatch.setattr("app.knowledge.bm25.BM25Plus", CountingBM25)
    source = MutableChunkSource(
        {
            TENANT_A: [chunk("a", "tenant-a", 0, content_hash="a" * 64)],
            TENANT_B: [chunk("b", "tenant-b", 0, content_hash="b" * 64)],
        }
    )
    index = TenantBM25PolicyIndex(source)

    assert (await index.search(TENANT_A, "规则"))[0].document_key == "tenant-a"
    await index.search(TENANT_A, "规则")
    assert (await index.search(TENANT_B, "规则"))[0].document_key == "tenant-b"
    source.chunks_by_tenant[TENANT_A] = [
        chunk("a", "tenant-a", 0, version=2, content_hash="c" * 64)
    ]
    await index.search(TENANT_A, "规则")

    assert source.calls == [TENANT_A, TENANT_A, TENANT_B, TENANT_A]
    assert len(builds) == 3


class ScriptedResult:
    def __init__(self, rows: Sequence[dict[str, Any]] = ()) -> None:
        self._rows = list(rows)

    def mappings(self) -> Self:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class ScriptedSession:
    def __init__(self, results: Sequence[ScriptedResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, object | None]] = []

    async def execute(
        self,
        statement: object,
        parameters: object | None = None,
    ) -> ScriptedResult:
        self.calls.append((str(statement), parameters))
        return self.results.pop(0)


class ScriptedDatabase:
    def __init__(self, results: Sequence[ScriptedResult]) -> None:
        self.session_instance = ScriptedSession(results)

    @asynccontextmanager
    async def session(self, tenant_id: UUID) -> AsyncIterator[ScriptedSession]:
        assert tenant_id == TENANT_A
        yield self.session_instance


def active_row() -> dict[str, object]:
    return {
        "chunk_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "document_id": "rework-policy",
        "document_key": "rework-policy",
        "title": "返工规则",
        "section": "链路",
        "text": "返工单必须关联根工单。",
        "ordinal": 0,
        "document_version": 2,
        "content_hash": "a" * 64,
    }


@pytest.mark.asyncio
async def test_active_chunk_source_filters_current_tenant_documents_and_chunks() -> None:
    database = ScriptedDatabase([ScriptedResult([active_row()])])
    source = PostgresActiveChunkSource(database)

    rows = await source.load_active_chunks(TENANT_A)

    assert rows[0].document_key == "rework-policy"
    sql, parameters = database.session_instance.calls[0]
    assert "d.tenant_id = :tenant_id" in sql
    assert "d.status = 'ACTIVE'" in sql
    assert "c.status = 'ACTIVE'" in sql
    assert "NOT EXISTS" in sql
    assert "newer.version > d.version" in sql
    assert "ORDER BY d.document_key, c.ordinal, c.id" in sql
    assert parameters == {"tenant_id": TENANT_A}


@pytest.mark.asyncio
async def test_vector_sql_sets_hnsw_and_filters_tenant_model_active_current_chunks() -> None:
    database = ScriptedDatabase([ScriptedResult(), ScriptedResult([active_row()])])
    vector = PostgresVectorPolicyIndex(database)

    rows = await vector.search(
        TENANT_A,
        [1.0] + [0.0] * 511,
        "synthetic-model/512",
        limit=50,
    )

    assert rows[0].chunk_id == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    setting_sql, _ = database.session_instance.calls[0]
    query_sql, parameters = database.session_instance.calls[1]
    assert "set_config('hnsw.ef_search', '100', true)" in setting_sql
    assert "e.tenant_id = :tenant_id" in query_sql
    assert "e.model_key = :model_key" in query_sql
    assert "d.status = 'ACTIVE'" in query_sql
    assert "c.status = 'ACTIVE'" in query_sql
    assert "NOT EXISTS" in query_sql
    assert "newer.version > d.version" in query_sql
    assert "e.content_hash = c.content_hash" in query_sql
    assert "<= :max_distance" in query_sql
    assert "e.embedding <=> CAST(:query_vector AS vector)" in query_sql
    assert "LIMIT :limit" in query_sql
    assert parameters["tenant_id"] == TENANT_A  # type: ignore[index]
    assert parameters["model_key"] == "synthetic-model/512"  # type: ignore[index]
    assert parameters["limit"] == 50  # type: ignore[index]
    assert parameters["max_distance"] == 0.45  # type: ignore[index]
