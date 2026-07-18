# 生产连接器与可观测性设计

**状态：** 书面规格已于 2026-07-18 确认
**日期：** 2026-07-18

## 1. 目标与范围

在不引入任何私有系统细节的前提下，提供可替换的工单后端端口、通用 HTTP 示例适配器、可靠同步、对账、链路追踪、指标和公开仓库隐私门禁。

## 2. 连接器端口

Java 定义 `WorkOrderConnector`，方法为：

```text
get(tenantContext, workOrderId)
search(tenantContext, criteria, page)
create(tenantContext, command, idempotencyKey)
assign(tenantContext, workOrderId, command, idempotencyKey)
update(tenantContext, workOrderId, command, expectedVersion, idempotencyKey)
transition(tenantContext, workOrderId, command, expectedVersion, idempotencyKey)
findByIdempotencyKey(tenantContext, operation, idempotencyKey)
```

返回统一 DTO 和稳定错误，不向 AI 层泄露上游响应结构。

## 3. 运行模式

### `local`

PostgreSQL 是事实源。连接器调用本地领域服务，适用于默认 Compose、开发、测试和公开演示。

### `http`

外部系统是事实源。本地 `work_order` 是租户隔离的只读投影；所有高风险命令在确认后同步调用外部系统，成功响应再更新投影和审计。

公开示例适配器使用通用资源路径和 MockServer/WireMock 契约，不包含任何真实企业路径、字段或认证配置。真实映射必须位于仓库外部部署配置或私有插件中。

## 4. 身份传播

连接器接收经过验证的 `TenantContext`，包含 tenant、subject、roles、project scopes、request/trace ID。外部认证支持：

- OAuth2 Client Credentials；
- 静态 Bearer Secret（仅部署 Secret Store）；
- mTLS。

用户 Token 默认不透传给外部系统；连接器使用服务身份，并把用户标识作为审计元数据发送。所有外部认证值使用 Secret 类型，禁止日志输出。

## 5. 可靠写入

- 每个外部命令携带稳定幂等键；
- 2xx 表示已确认成功；
- 409 若指向相同幂等键则查询并复用既有结果；
- 4xx 业务失败不重试；
- 429、502、503 可在确认未接收或上游明确支持幂等时有限重试；
- 网络超时和连接中断标记为 `UNKNOWN`；
- `UNKNOWN` 必须调用 `findByIdempotencyKey` 对账，不能直接再次写入；
- 对账成功后补写本地投影、领域事件和建议执行结果。

## 6. 同步与对账

外部模式支持 Webhook 和增量轮询，两者都写 `inbox_message` 去重。Webhook 先验证签名和时间窗，再按外部事件版本更新投影。轮询使用每租户 `sync_cursor`，分页读取 `updated_since`。

每日对账比较外部版本、状态和负责人。差异只生成告警和修复任务，不静默覆盖本地审计历史。

## 7. 可观测性

使用 OpenTelemetry 串联：

```text
HTTP request -> AI route -> retrieval/model -> proposal -> Java command
-> database/outbox -> connector -> external system -> reconciliation
```

Span 属性允许 request ID、tenant UUID、proposal/job ID、连接器类型、上游状态和错误码；禁止 Prompt、SQL 参数值、Token、联系方式和完整响应体。

Prometheus 指标包括：

- 建议生成、确认、拒绝、过期和失败；
- 工单命令延迟、冲突和幂等复用；
- 连接器成功、超时、未知、对账恢复和断路器状态；
- 质检任务、重试、模型耗时、Token 和费用；
- NL2SQL 生成、拒绝、成本、超时和返回行数；
- BM25、vector、hybrid 和降级次数。

## 8. 隐私与仓库门禁

CI 对源码、配置、文档、Git diff 和容器构建上下文执行扫描，拒绝：

- 私有绝对路径和内部包名；
- 私有主机、端口、仓库地址和接口路径；
- 公司、客户、人员或项目标识；
- API Key、Token、证书、数据库密码和真实图片 URL；
- 从私有实现复制的长文本片段。

示例数据继续使用虚构名称，真实连接配置只能通过环境变量和 Secret Store 注入。

## 9. 测试与验收

- Local 与 HTTP 连接器通过同一契约测试套件；
- Mock 上游覆盖成功、重复、业务冲突、429、5xx、超时、坏响应和查询幂等结果；
- 超时后对账证明不会重复写入；
- Webhook 签名、重放时间窗、版本乱序和 Inbox 去重测试；
- 两租户使用不同连接凭证和游标，任何上下文不得串用；
- Trace 贯穿 AI、Java、数据库和 Mock 上游；
- 日志捕获测试证明敏感字段被删除或脱敏；
- CI 隐私扫描使用合成违规夹具验证规则确实会失败；
- 私有参考仓库始终保持未修改、未暂存、未提交和未推送。
