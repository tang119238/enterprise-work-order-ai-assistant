# pgvector 混合召回 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将进程内单租户 BM25 升级为持久化、租户隔离、可增量摄取的 BM25 + pgvector RRF 混合检索，并在向量不可用时显式降级。

**Architecture:** Python AI 服务使用独立 `ai_app` 连接和 Alembic 管理知识表；文档版本、分块和 512 维嵌入持久化到 PostgreSQL 16 + pgvector。检索并行获取 BM25 Top 50 与向量 Top 50，以 RRF 融合并返回 Top 5。嵌入提供方通过 Protocol 隔离，默认 FastEmbed，本地测试使用确定性向量。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, asyncpg, Alembic, pgvector, FastEmbed, jieba, rank-bm25, pytest, Testcontainers/PostgreSQL 16.

## Global Constraints

- 依赖阶段 1 已建立的租户身份和 `ai_app` 数据库角色；Python 不写 Java 工单事实表。
- v1 嵌入维度固定为 512；维度不匹配在写数据库前失败。
- 引用 quote 必须逐字来自当前 `ACTIVE` 分块；工单描述不得进入制度知识库。
- 只有两路检索都成功才标记 `hybrid`；任何降级都必须返回稳定 warning。
- 私有参考项目保持只读；所有文档、数据和配置继续使用合成内容。

---

## File Structure Map

```text
apps/ai-service/alembic.ini
apps/ai-service/alembic/env.py
apps/ai-service/alembic/versions/20260718_01_knowledge_pgvector.py
apps/ai-service/app/db.py
apps/ai-service/app/knowledge/
  repository.py ingest.py hybrid.py
  embedding/{base,deterministic,fastembed_provider,openai_compatible}.py
apps/ai-service/tests/knowledge/
  test_repository.py test_ingest.py test_embedding.py test_hybrid.py
  test_pgvector_integration.py
eval/hybrid_questions.json
eval/run_retrieval_eval.py
```

## Task 1: Add pgvector runtime and Python persistence dependencies

**Files:**
- Modify: `docker-compose.yml`
- Modify: `apps/ai-service/pyproject.toml`
- Modify: `apps/ai-service/Dockerfile`
- Modify: `.env.example`
- Test: `apps/ai-service/tests/knowledge/test_pgvector_configuration.py`

**Interfaces:**
- Compose database image is `pgvector/pgvector:pg16` and exposes the existing database only internally.
- Python runtime settings add `AI_DATABASE_URL`, `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS=512`, and `FASTEMBED_CACHE_PATH`; Alembic consumes a separate `AI_MIGRATION_DATABASE_URL` owned by the migration role.

- [ ] **Step 1: Write RED configuration tests**

```python
def test_vector_configuration_is_fixed_to_512() -> None:
    settings = Settings()
    assert settings.embedding_dimensions == 512
    assert settings.embedding_provider == "local"

def test_compose_uses_pg16_vector_image() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert "pgvector/pgvector:pg16" in compose
    assert "fastembed-cache:/models" in compose
```

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/knowledge/test_pgvector_configuration.py -q
```

Expected: missing settings and image assertions fail.

- [ ] **Step 3: Add exact dependencies and container cache**

Add `sqlalchemy[asyncio]>=2.0,<3`, `asyncpg>=0.30,<1`, `alembic>=1.15,<2`, `pgvector>=0.4,<1`, and `fastembed>=0.7,<1`; add test dependency `testcontainers[postgres]>=4.10,<5`. Mount a named `fastembed-cache` volume at `/models`; never bake downloaded model weights into Git or the image.

- [ ] **Step 4: Run GREEN and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/knowledge/test_pgvector_configuration.py -q
git add docker-compose.yml apps/ai-service/pyproject.toml apps/ai-service/Dockerfile apps/ai-service/app/config.py apps/ai-service/tests/knowledge/test_pgvector_configuration.py .env.example
git commit -m "build(ai): add pgvector retrieval runtime"
```

## Task 2: Create tenant-aware knowledge schema and RLS

**Files:**
- Create: `apps/ai-service/alembic.ini`
- Create: `apps/ai-service/alembic/env.py`
- Create: `apps/ai-service/alembic/versions/20260718_01_knowledge_pgvector.py`
- Create: `apps/ai-service/app/db.py`
- Test: `apps/ai-service/tests/knowledge/test_pgvector_integration.py`

**Interfaces:**
- Produces `knowledge_document`, `knowledge_chunk`, `knowledge_embedding`, `embedding_job` with tenant-prefixed keys and FORCE RLS.
- `knowledge_embedding.embedding` is exactly PostgreSQL `vector(512)` and rejects every other dimension.
- `Database.session(tenant_id)` sets transaction-local `app.tenant_id` before any query.

- [ ] **Step 1: Write RED Testcontainers tests**

```python
async def test_schema_enforces_vector_dimensions_and_tenant_rls(db: TestDatabase) -> None:
    assert await db.scalar("select extversion from pg_extension where extname='vector'")
    await db.insert_active_chunk(TENANT_A, "same-key", [0.0] * 512)
    assert await db.count_chunks(TENANT_A) == 1
    assert await db.count_chunks(TENANT_B) == 0
    with pytest.raises(Exception):
        await db.insert_embedding(TENANT_A, [0.0] * 511)
```

Also query `pg_indexes` and assert `vector_cosine_ops`, `m=16`, `ef_construction=64`.

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/knowledge/test_pgvector_integration.py -q
```

Expected: Alembic and database helpers are absent.

- [ ] **Step 3: Implement the migration**

The revision executes `CREATE EXTENSION IF NOT EXISTS vector`, creates the four approved tables, all uniqueness/check constraints, `(tenant_id, model_key)` B-tree index, and the exact HNSW index. RLS policy shape matches phase 1. Grant only knowledge-table CRUD to `ai_app`; do not grant work-order tables.

- [ ] **Step 4: Implement session scoping**

```python
@asynccontextmanager
async def session(self, tenant_id: UUID) -> AsyncIterator[AsyncSession]:
    async with self._session_factory.begin() as session:
        await session.execute(
            text("select set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        yield session
```

- [ ] **Step 5: Run GREEN and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/knowledge/test_pgvector_integration.py -q
git add apps/ai-service/alembic.ini apps/ai-service/alembic apps/ai-service/app/db.py apps/ai-service/tests/knowledge/test_pgvector_integration.py
git commit -m "feat(ai): persist tenant knowledge in pgvector"
```

## Task 3: Implement fixed-dimension embedding providers

**Files:**
- Create: `apps/ai-service/app/knowledge/embedding/base.py`
- Create: `apps/ai-service/app/knowledge/embedding/deterministic.py`
- Create: `apps/ai-service/app/knowledge/embedding/fastembed_provider.py`
- Create: `apps/ai-service/app/knowledge/embedding/openai_compatible.py`
- Create: `apps/ai-service/app/knowledge/embedding/registry.py`
- Test: `apps/ai-service/tests/knowledge/test_embedding.py`

**Interfaces:**
- `EmbeddingProvider.embed(texts: Sequence[str]) -> list[list[float]]` and properties `model_key`, `dimensions`, `loaded`.
- Providers are `local`, `openai_compatible`, `disabled`; tests inject `DeterministicEmbeddingProvider(512)`.

- [ ] **Step 1: Write RED provider contract tests**

```python
@pytest.mark.parametrize("provider", [DeterministicEmbeddingProvider(512), fake_fastembed(), fake_openai()])
async def test_provider_returns_one_normalized_512_vector_per_text(provider: EmbeddingProvider) -> None:
    vectors = await provider.embed(["返工规则", "紧急工单"])
    assert [len(vector) for vector in vectors] == [512, 512]
    assert all(math.isclose(sum(x * x for x in vector), 1.0, rel_tol=1e-5) for vector in vectors)
```

Test OpenAI-compatible timeout/bad status/bad dimension and ensure authorization values never appear in exception text.

- [ ] **Step 2: Run RED, then implement the Protocol and adapters**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/knowledge/test_embedding.py -q
```

FastEmbed uses `BAAI/bge-small-zh-v1.5`, `cache_dir=settings.fastembed_cache_path`, and runs blocking inference through `asyncio.to_thread`. Startup dimension probing must reject anything other than 512. `disabled` returns a capability error rather than zero vectors.

- [ ] **Step 3: Run GREEN and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/knowledge/test_embedding.py -q
git add apps/ai-service/app/knowledge/embedding apps/ai-service/tests/knowledge/test_embedding.py
git commit -m "feat(ai): add pluggable 512 dimension embeddings"
```

## Task 4: Add idempotent document ingestion and embedding jobs

**Files:**
- Create: `apps/ai-service/app/knowledge/repository.py`
- Create: `apps/ai-service/app/knowledge/ingest.py`
- Modify: `apps/ai-service/app/knowledge/models.py`
- Test: `apps/ai-service/tests/knowledge/test_repository.py`
- Test: `apps/ai-service/tests/knowledge/test_ingest.py`

**Interfaces:**
- `KnowledgeIngestor.ingest(tenant_id, document_key, title, markdown, source_uri) -> IngestResult`.
- `EmbeddingWorker.claim(tenant_id, limit)`, `succeed(job_id, vector)`, `retry(job_id, code, next_retry_at)` use CAS status updates.
- Unchanged content returns `skipped=True`; changed content creates the next integer document version.

- [ ] **Step 1: Write RED ingestion tests**

Cover stable SHA-256, existing Markdown chunk rules, no-op duplicate ingestion, changed version, unique chunk keys, idempotent jobs, worker CAS, old version remaining inactive, and activation only after every job succeeds.

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/knowledge/test_repository.py apps/ai-service/tests/knowledge/test_ingest.py -q
```

- [ ] **Step 3: Implement ingestion in two transaction boundaries**

Transaction A writes `PENDING` document/chunks/jobs or returns unchanged. Worker embeds outside a transaction, then transaction B upserts `(tenant_id, chunk_id, model_key)` and marks the job `SUCCEEDED`. When no unfinished job remains, atomically set previous version `INACTIVE` and new version `ACTIVE`. Failed downloads create `RETRY_WAIT`; they never activate a partial version.

- [ ] **Step 4: Run GREEN and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/knowledge/test_repository.py apps/ai-service/tests/knowledge/test_ingest.py -q
git add apps/ai-service/app/knowledge apps/ai-service/tests/knowledge
git commit -m "feat(ai): ingest versioned tenant policy knowledge"
```

## Task 5: Implement BM25/vector RRF fusion and explicit degradation

**Files:**
- Create: `apps/ai-service/app/knowledge/hybrid.py`
- Modify: `apps/ai-service/app/knowledge/bm25.py`
- Modify: `apps/ai-service/app/knowledge/models.py`
- Modify: `apps/ai-service/app/agent/graph.py`
- Modify: `apps/ai-service/app/agent/state.py`
- Test: `apps/ai-service/tests/knowledge/test_hybrid.py`
- Modify: `apps/ai-service/tests/agent/test_graph.py`

**Interfaces:**
- `HybridPolicyIndex.search(tenant_id: UUID, query: str, limit: int = 5) -> RetrievalResult`.
- `RetrievalHit` carries `bm25_rank`, `vector_rank`, `rrf_score`; `RetrievalResult` carries mode and warnings.
- RRF contribution is exactly `1 / (60 + rank)` with one-based rank.

- [ ] **Step 1: Write RED deterministic ranking tests**

```python
async def test_rrf_merges_and_stably_sorts() -> None:
    result = await index.search(TENANT_A, "返工", limit=5)
    assert [hit.chunk_id for hit in result.hits] == ["shared", "bm25-only", "vector-only"]
    assert result.hits[0].rrf_score == pytest.approx(1 / 61 + 1 / 62)
    assert result.mode == "hybrid"
```

Also assert Top 50 is requested from each side, tie order is `(document_key, ordinal)`, vector-only results work, empty result has no citations, and vector failure returns BM25 plus `HYBRID_RETRIEVAL_DEGRADED`.

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/knowledge/test_hybrid.py apps/ai-service/tests/agent/test_graph.py -q
```

- [ ] **Step 3: Implement query boundaries**

Vector SQL must set `hnsw.ef_search=100`, filter tenant/model/current active document, order by `embedding <=> :query_vector`, and limit 50. BM25 caches only current active chunks per tenant/version and invalidates after activation. Fuse by chunk UUID, then truncate to requested limit. Pass request `tenant_id` from authenticated chat context; never trust a tenant ID in the message body.

- [ ] **Step 4: Run GREEN and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/knowledge/test_hybrid.py apps/ai-service/tests/agent/test_graph.py -q
git add apps/ai-service/app/knowledge apps/ai-service/app/agent apps/ai-service/tests/knowledge apps/ai-service/tests/agent/test_graph.py
git commit -m "feat(ai): fuse BM25 and pgvector retrieval"
```

## Task 6: Surface capability health and lifecycle workers

**Files:**
- Modify: `apps/ai-service/app/main.py`
- Modify: `apps/ai-service/app/config.py`
- Create: `apps/ai-service/app/knowledge/worker.py`
- Modify: `apps/ai-service/tests/api/test_chat.py`
- Create: `apps/ai-service/tests/api/test_retrieval_health.py`

**Interfaces:**
- `/health` adds `retrieval.configured`, `retrieval.model_loaded`, `retrieval.last_embedding_success_at`, and `retrieval.mode` without exposing secrets.
- FastAPI lifespan starts one bounded embedding worker and closes DB/http/model resources on shutdown.

- [ ] **Step 1: Write RED health/lifespan tests**

Test configured-but-not-loaded, disabled, recent success, first-download failure, graceful shutdown, and a chat response that includes degraded warning.

- [ ] **Step 2: Run RED, implement, run GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/api/test_retrieval_health.py apps/ai-service/tests/api/test_chat.py -q
```

The worker polls with a cancellable event, claims at most 20 jobs, uses the approved backoff, and never blocks FastAPI startup on first model download.

- [ ] **Step 3: Commit**

```powershell
git add apps/ai-service/app/main.py apps/ai-service/app/config.py apps/ai-service/app/knowledge/worker.py apps/ai-service/tests/api
git commit -m "feat(ai): report and operate retrieval capability"
```

## Task 7: Add retrieval evaluation and phase acceptance

**Files:**
- Create: `eval/hybrid_questions.json`
- Create: `eval/run_retrieval_eval.py`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `scripts/smoke_test.py`
- Test: `apps/ai-service/tests/eval/test_retrieval_evaluator.py`

**Interfaces:**
- Evaluation reports BM25 baseline Recall@5, hybrid Recall@5, citation validity and degraded-request count.
- Dataset includes synonymous rewrites, conversational queries, keyword-absent queries and hard negatives.

- [ ] **Step 1: Write RED evaluator tests and a checked-in synthetic dataset**

Require at least 30 retrieval questions, every positive case to name current synthetic `document_id`, and every hard negative to expect no citation. Unit-test score calculations before running live Compose.

- [ ] **Step 2: Run complete verification**

```powershell
.\.venv\Scripts\python.exe -m ruff check apps/ai-service
.\.venv\Scripts\python.exe -m mypy apps/ai-service/app
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests -q
docker compose up --build -d
python scripts/smoke_test.py
python eval/run_retrieval_eval.py --base-url http://localhost:8000
docker compose down
git diff --check
```

Expected: all checks pass; hybrid Recall@5 is not below BM25 baseline and is at least 90% on the expanded set; citation validity is at least 95%.

- [ ] **Step 3: Commit the verified phase**

```powershell
git add eval README.md docs/architecture.md scripts/smoke_test.py
git commit -m "test(ai): verify tenant hybrid retrieval quality"
git status --short --branch
```

Expected: clean worktree and no push.
