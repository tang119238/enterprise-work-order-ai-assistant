# NL2SQL 分析查询 API

## 概述

`POST /analytics/query` 允许拥有 `ANALYST` 角色的用户用自然语言查询工单、质检和整改统计数据。系统会：

1. 使用 LLM 将自然语言转换为 SQL
2. 通过 Python sqlglot 和 Java JSQLParser 进行双层安全校验
3. 对 `analytics_reader` 只读连接执行查询
4. 返回结构化结果，包含 SQL、列名、行数据和审计 ID

## 认证与授权

- 需要有效的 JWT Bearer Token
- 用户必须拥有 `ANALYST` 角色
- 用户必须有非空的项目范围（`project_ids`）
- 查询自动按租户和项目范围过滤，无法跨租户访问

## 请求

```http
POST /analytics/query
Content-Type: application/json
Authorization: Bearer <jwt-token>

{
  "question": "各状态的工单数量是多少？"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `question` | string | 是 | 自然语言分析问题，1-1000 字符 |

## 响应

```json
{
  "answer": "查询返回 5 行结果。\n\nstatus | count\n--- | ---\nCLOSED | 15\nCOMPLETED | 12\n...",
  "sql": "SELECT status, COUNT(*) AS count FROM analytics_work_order_v GROUP BY status ORDER BY count DESC LIMIT 200",
  "columns": ["status", "count"],
  "rows": [["CLOSED", 15], ["COMPLETED", 12], ["PROCESSING", 8], ["PENDING_ACCEPTANCE", 10], ["PENDING_DISPATCH", 5]],
  "truncated": false,
  "audit_id": "a1b2c3d4-...",
  "latency_ms": 1234
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `answer` | string | 确定性文本汇总，仅基于返回行数据 |
| `sql` | string | 实际执行的 SQL，可用于审计 |
| `columns` | string[] | 列名列表 |
| `rows` | array[] | 行数据，最多 200 行 |
| `truncated` | boolean | 是否因行数/字节数限制被截断 |
| `audit_id` | string | 审计记录 ID |
| `latency_ms` | int | 总耗时（毫秒） |

## 可查询的视图

| 视图 | 说明 |
|------|------|
| `analytics_work_order_v` | 工单统计：状态、优先级、类型、负责人、创建/完成时间、是否超期 |
| `analytics_quality_v` | 质检结果：判定、置信度、模型信息、发现统计 |
| `analytics_rectification_v` | 整改案例：状态、轮次、解决耗时 |

> 注意：视图不暴露描述全文、联系方式、附件 URL 等敏感字段。

## 安全机制

### 双层 SQL 校验

1. **Python 层（sqlglot）**：AST 解析，校验表/列/函数白名单，阻止注入
2. **Java 层（JSQLParser）**：独立重验，EXPLAIN 成本评估

### 执行边界

- 只读连接（`analytics_reader` 角色）
- `SET TRANSACTION READ ONLY`
- `statement_timeout = 3000ms`
- 最多 200 行、50 列、1MB 响应

### 被阻止的操作

- INSERT/UPDATE/DELETE/DROP/TRUNCATE
- 系统表访问（`pg_catalog`、`information_schema`）
- 危险函数（`pg_sleep`、`lo_import` 等）
- 多语句、注释、UNION、递归 CTE

## 错误码

| HTTP | 错误码 | 说明 |
|------|--------|------|
| 401 | `AUTHENTICATED_TENANT_REQUIRED` | 未认证 |
| 403 | `ANALYTICS_NOT_PERMITTED` | 无 ANALYST 角色或项目范围为空 |
| 422 | `SQL_GENERATION_INVALID` | 模型输出不可解析 |
| 422 | `SQL_POLICY_VIOLATION` | SQL 违反安全策略 |
| 422 | `SQL_COST_LIMIT_EXCEEDED` | EXPLAIN 估算成本超限 |
| 503 | `ANALYTICS_UNAVAILABLE` | 只读数据源不可用 |
| 504 | `SQL_EXECUTION_TIMEOUT` | 执行超时（>3s） |

所有拒绝都返回 `audit_id`，不生成替代 SQL。

## 示例问题

- 各状态的工单数量是多少？
- P0 优先级的工单有多少？
- 质检判定为 FAIL 的工单有哪些？
- 各项目的质检通过率
- 整改案例中状态为 CLOSED 的有多少？
- 最近创建的 10 个工单
- 已完成工单的平均处理时长

## 评测

```bash
python eval/run_nl2sql_eval.py --base-url http://localhost:8000
```

评测集包含 15 个合法查询和 19 个攻击用例。验收门槛：危险 SQL 拦截率 100%。
