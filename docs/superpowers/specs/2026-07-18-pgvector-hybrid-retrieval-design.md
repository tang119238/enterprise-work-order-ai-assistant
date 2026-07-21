# pgvector 与混合召回设计

**状态：** 书面规格已于 2026-07-18 确认
**日期：** 2026-07-18

## 1. 目标与范围

把当前进程内 BM25 索引升级为租户感知、可持久化、可增量更新的 BM25 + pgvector 混合召回。引用仍必须来自真实知识分块。

本阶段只索引制度知识，不把工单描述混入制度引用库。

## 2. 组件边界

Python AI 服务拥有知识数据和检索实现，使用 `ai_app` 数据库角色。Java 工单服务不直接写知识表。检索接口继续满足现有 `PolicyIndex.search` 语义，并增加显式 `tenant_id` 参数。

## 3. 数据模型

### `knowledge_document`

`id UUID`、`tenant_id UUID`、`document_key`、`title`、`source_type`、`source_uri`、`content_hash`、`version`、`status`、审计时间。`(tenant_id, document_key, version)` 唯一。

### `knowledge_chunk`

`id UUID`、`tenant_id UUID`、`document_id UUID`、`chunk_key`、`section`、`content`、`content_hash`、`token_count`、`ordinal`、`status`。`(tenant_id, document_id, chunk_key)` 唯一。

### `knowledge_embedding`

`chunk_id UUID`、`tenant_id UUID`、`model_key`、`dimensions SMALLINT`、`embedding vector(512)`、`content_hash`、`embedded_at`。`(tenant_id, chunk_id, model_key)` 唯一。

v1 统一要求嵌入输出为 512 维。更换维度必须通过新迁移、新列或新表完成，不能在同一 HNSW 索引混用维度。

### `embedding_job`

保存文档/分块目标、业务幂等键、状态、重试次数、错误码、下次重试时间和模型版本。状态为 `PENDING`、`RUNNING`、`RETRY_WAIT`、`SUCCEEDED`、`FAILED`、`SKIPPED`。

## 4. PostgreSQL 与索引

Compose 数据库镜像切换为兼容 PostgreSQL 16 的 pgvector 镜像，Flyway 执行 `CREATE EXTENSION IF NOT EXISTS vector`。

向量索引固定为余弦距离：

```sql
CREATE INDEX idx_knowledge_embedding_hnsw
ON knowledge_embedding
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

同时为 `(tenant_id, model_key)` 建 B-tree 索引。查询在事务内设置 `hnsw.ef_search = 100`，并始终包含租户和模型条件。

## 5. 嵌入提供方

- `local`：默认，通过 FastEmbed 运行 `BAAI/bge-small-zh-v1.5` ONNX 模型，输出 512 维并把模型缓存到独立 Docker volume；
- `openai_compatible`：调用配置的 Embeddings 端点，启动时验证输出维度为 512；
- `disabled`：只允许 BM25，并在健康检查中显示未启用向量召回。

测试使用确定性的 512 维测试提供方验证数据库和排序，不把测试向量宣称为真实语义能力。

健康检查必须区分“配置完成”“模型已加载”和“最近一次嵌入成功”。首次模型下载失败时服务可以启动并使用 BM25，但健康详情必须标记向量能力不可用。

## 6. 文档摄取

1. 校验稳定 `document_key`；
2. 使用现有 Markdown 标题和长度规则切分；
3. 计算文档及分块 SHA-256；
4. 内容未变化则跳过；
5. 在事务中写 document/chunk；
6. 创建幂等 embedding jobs；
7. 嵌入成功后原子切换文档版本为 `ACTIVE`；
8. 旧版本保留审计时间，检索只读当前版本。

## 7. 混合查询

对同一租户执行：

1. BM25 召回 Top 50；
2. pgvector 余弦召回 Top 50；
3. 以分块 ID 合并；
4. 使用 `score += 1 / (60 + rank)` 做 RRF；
5. 分数相同时按 `document_key`、`ordinal` 稳定排序；
6. 返回 Top 5。

响应记录每个结果的 `bm25_rank`、`vector_rank` 和 `rrf_score`，但引用只包含文档、章节和原文 quote。

## 8. 降级语义

- 嵌入提供方不可用：新摄取任务进入重试，不影响已索引文档；
- pgvector 查询失败：使用 BM25 并返回 `HYBRID_RETRIEVAL_DEGRADED`；
- BM25 无结果但向量有结果：允许返回向量结果；
- 两路均无结果：返回“知识库没有足够依据”，不得生成引用；
- 只有两路都成功时，响应才标记 `retrieval_mode=hybrid`。

## 9. 测试与评测

- Testcontainers 验证 vector 扩展、512 维约束、HNSW 和租户过滤；
- 单元测试验证 BM25、向量、RRF、稳定排序及降级警告；
- 文档重复摄取不得产生重复 chunk 或 embedding；
- 两租户使用相同 `document_key` 时互不可见；
- 扩充评测集，加入同义改写、口语问法、关键词缺失和硬负例；
- Recall@5 不低于 BM25 基线，扩展集至少 90%；
- 引用有效率至少 95%，结构化 quote 必须逐字存在于当前活动分块。
