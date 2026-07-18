# 多租户工单写入与操作确认设计

**状态：** 书面规格已于 2026-07-18 确认
**日期：** 2026-07-18

## 1. 目标与范围

本子项目把只读 Spring Boot 服务升级为受身份、租户、项目范围、状态机和确认流程保护的工单命令服务。范围包括创建、派单、允许字段修改、接单、开始处理、提交完成、关闭和取消。

本阶段不实现模型质检、NL2SQL、真实企业连接器映射或前端管理页面。

## 2. 身份与授权

Spring Security 以 OAuth2 Resource Server 模式验证 JWT 的签名、`iss`、`aud`、`exp` 和 `nbf`。标准声明映射为：

- `sub`：稳定用户标识；
- `tenant_id`：当前租户；
- `roles`：租户角色；
- `project_ids`：当前会话可访问项目；
- `scope`：服务调用权限。

开发环境使用测试专用签名密钥和固定合成身份；生产 profile 不包含令牌签发端点。

| 角色 | 命令权限 |
| --- | --- |
| `TENANT_ADMIN` | 租户内全部配置和审计读取 |
| `DISPATCHER` | 创建、派单、允许字段修改、取消 |
| `OPERATOR` | 本人或团队范围内接单、处理、提交完成 |
| `QUALITY_REVIEWER` | 完成后的复核、关闭、整改确认 |
| `AI_SERVICE` | 创建操作建议，不得确认建议 |

Java 在 Controller 入口和领域命令处理器内各执行一次授权。项目范围不匹配时统一返回 `404`，避免暴露资源存在性。

有效项目范围取 JWT `project_ids` 与数据库中当前 `project_scope` 的交集；角色取 JWT 与当前 `tenant_membership` 均认可的集合。这样撤销成员或项目权限后，无需等待旧 Token 过期即可阻止高风险写入。

## 3. 数据模型

### `tenant`

`id UUID`、`tenant_key VARCHAR(64)`、`name`、`status`、审计时间。`tenant_key` 全局唯一。

### `user_identity`

`id UUID`、`issuer`、`subject`、`display_name`、`status`。`(issuer, subject)` 唯一，不保存密码。

### `tenant_membership` 与 `project_scope`

成员表保存租户角色；项目范围表保存成员可访问的合成项目。两表都以 `tenant_id` 开头建立复合索引。

### `work_order`

使用 `id UUID` 内部主键；`(tenant_id, work_order_no)` 唯一。保留现有标题、描述、项目、空间、类型、优先级、状态、来源、负责人和返工字段，并增加：

- `project_id UUID`；
- `assignee_id UUID NULL`；
- `version BIGINT NOT NULL DEFAULT 0`；
- `created_by`、`updated_by`；
- `cancelled_at`、`cancel_reason`。

现有 50 条工单迁移为两个合成租户各 25 条，工单号只要求租户内唯一。

### `action_proposal`

保存 `action_type`、`target_id`、`command_payload JSONB`、`before_snapshot JSONB`、`after_snapshot JSONB`、`risk_level`、`status`、`requested_by`、`confirmed_by`、`expected_version`、`expires_at`、`execution_result JSONB` 和 `error_code`。

状态为 `PENDING_CONFIRMATION`、`CONFIRMED`、`REJECTED`、`EXPIRED`、`EXECUTING`、`EXECUTED`、`FAILED`。

### 审计和可靠性表

- `work_order_event`：不可变 before/after、命令、操作人、request/trace ID；
- `work_order_assignment`：每次负责人变化的时间区间与原因；
- `idempotency_record`：`(tenant_id, operation, idempotency_key)` 唯一；
- `outbox_event`：事务内写入待投递事件；
- `inbox_message`：外部消息去重。

## 4. 状态机

```text
PENDING_DISPATCH -> PENDING_ACCEPTANCE -> PROCESSING -> COMPLETED -> CLOSED
PENDING_DISPATCH | PENDING_ACCEPTANCE | PROCESSING | COMPLETED -> CANCELLED
```

- 创建后初始状态为 `PENDING_DISPATCH`。
- 派单同时写 assignment 历史并进入 `PENDING_ACCEPTANCE`。
- 只有当前负责人可以接单和开始处理。
- `COMPLETED` 表示处理人已提交，`CLOSED` 表示质检或有权限人员已关闭。
- `CLOSED` 和 `CANCELLED` 不接受字段修改或状态回退。
- 取消必须包含原因。

## 5. API 契约

### 建议接口

```http
POST /api/action-proposals
GET  /api/action-proposals/{proposalId}
POST /api/action-proposals/{proposalId}/confirm
POST /api/action-proposals/{proposalId}/reject
```

创建建议请求包含 `action_type`、结构化参数和可选目标工单；服务器忽略客户端的租户和 before/after，使用当前身份及数据库快照重新生成权威预览。

确认请求必须携带 `Idempotency-Key`。确认处理器校验同一租户、建议未过期、确认人不是 `AI_SERVICE`、权限仍有效且 `expected_version` 等于当前工单版本。

### 查询接口

现有详情、分页和返工链路接口继续存在，但全部使用当前租户和项目范围。分页返回值不泄露其他租户总数。

## 6. 写入事务

确认成功后的单个事务按顺序执行：

1. 抢占 `action_proposal`：`CONFIRMED -> EXECUTING`；
2. 以 `id + tenant_id + version` 更新工单；
3. 写 assignment（如适用）；
4. 写 `work_order_event`；
5. 写 `outbox_event`；
6. 保存幂等响应；
7. 标记建议 `EXECUTED`。

任何步骤失败都回滚。乐观锁失败返回 `409 WORK_ORDER_VERSION_CONFLICT` 和新的预览，不自动覆盖。

## 7. RLS

所有租户表启用并强制 RLS。每个事务在连接上执行 `SET LOCAL app.tenant_id`，策略使用 `current_setting('app.tenant_id', true)`。运行时角色不是表所有者且没有 `BYPASSRLS`。

测试必须证明：缺失租户上下文默认拒绝、错误租户不可读写、同租户跨项目仍由应用授权拒绝。

## 8. 错误语义

| HTTP | 错误码 | 场景 |
| ---: | --- | --- |
| 401 | `AUTHENTICATION_REQUIRED` | Token 缺失或无效 |
| 403 | `ACTION_NOT_PERMITTED` | 已知操作但角色无权执行 |
| 404 | `WORK_ORDER_NOT_FOUND` | 不存在或不在项目范围 |
| 409 | `WORK_ORDER_VERSION_CONFLICT` | 版本变化 |
| 409 | `INVALID_STATE_TRANSITION` | 状态流转不合法 |
| 410 | `ACTION_PROPOSAL_EXPIRED` | 建议过期 |
| 422 | `INVALID_COMMAND` | 字段或业务校验失败 |

## 9. 测试与验收

- 领域状态机表驱动测试覆盖每个允许和拒绝的转换；
- Controller 测试覆盖 JWT、角色、项目范围和稳定错误体；
- PostgreSQL Testcontainers 覆盖 Flyway、RLS、乐观锁、幂等和事务回滚；
- 两个并发确认只能有一个执行工单写入；
- 相同幂等键重复请求返回同一结果；
- 两个租户的详情、分页、返工链路、建议和审计全部隔离；
- `AI_SERVICE` 确认建议必须失败；
- 现有只读 API 的租户内行为保持兼容。
