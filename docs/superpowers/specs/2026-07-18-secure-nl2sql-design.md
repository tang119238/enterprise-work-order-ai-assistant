# 安全 NL2SQL 设计

**状态：** 书面规格已于 2026-07-18 确认
**日期：** 2026-07-18

## 1. 目标与范围

允许有 `ANALYST` 角色的用户用自然语言查询工单、质检和整改统计，同时保证模型生成的 SQL 只能访问脱敏、租户隔离、只读的分析视图。

v1 支持 PostgreSQL 单条 `SELECT`、聚合、分组、排序、受限 CTE 和受限 JOIN。不支持写操作、跨库、存储过程、系统目录、任意业务原表或用户上传 SQL。

## 2. 分析视图

Java Flyway 创建三个稳定视图：

- `analytics_work_order_v`：工单数量、状态、优先级、类型、项目、负责人显示名、创建/完成/SLA 时间；
- `analytics_quality_v`：质检判定、严重度、规则编码、模型版本和轮次；
- `analytics_rectification_v`：整改状态、轮次、创建及关闭时间。

视图不暴露描述全文、联系方式、附件 URL、内部 Token、删除记录或模型原始响应。视图使用 PostgreSQL 16 的 `security_invoker=true`，使查询者权限和底层 RLS 生效。每个视图包含 `tenant_id` 和 `project_id`；事务设置 `app.tenant_id` 与 UUID 数组格式的 `app.project_ids`，RLS 同时要求租户相等且项目属于该数组。缺失任一上下文时默认拒绝。

## 3. 语义目录

Python 维护版本化目录，定义视图、列、中文同义词、数据类型、枚举值、允许 JOIN 和示例问题。目录由代码生成并测试，不从数据库任意注释或知识文档拼接，避免提示注入改变 Schema。

## 4. API

```http
POST /analytics/query
```

该端点位于 FastAPI。Python 通过服务身份调用 Java 内部端点 `POST /internal/analytics/execute`；内部端点仍要求用户租户、项目范围和 trace 上下文，且只接受 Python 生成的 SQL 与语义目录版本，不接受面向用户的任意数据库参数。

请求只包含 `question`。响应包含：

```json
{
  "answer": "确定性汇总或模型解释",
  "sql": "实际执行的 SELECT",
  "columns": ["status", "count"],
  "rows": [],
  "truncated": false,
  "audit_id": "uuid",
  "latency_ms": 0
}
```

结果解释只能依据返回行，结构化 SQL 和行集始终一并返回以便审计。

## 5. 双层校验

### Python 规划层

使用 SQL AST 解析器验证：

- 恰好一条语句且根节点为 `SELECT`；
- 只引用目录中的视图、列、函数和 JOIN；
- 禁止注释、字符串拼接出的标识符、锁、DDL/DML、COPY、文件、网络和系统函数；
- 没有 `LIMIT` 时添加 `LIMIT 200`，更大限制收敛到 200。

### Java 执行层

Java 使用独立解析器重新解析原始 SQL，重复执行 allowlist 校验，并验证当前 JWT、租户和项目范围。Java 不信任 Python 传来的“已校验”标志。

执行前运行 `EXPLAIN (FORMAT JSON)`，拒绝估算成本超过 100000 或估算行数超过 1000000 的查询。

## 6. 数据库执行边界

- 使用 `analytics_reader`；
- `SET TRANSACTION READ ONLY`；
- `statement_timeout = 3000ms`；
- `lock_timeout = 500ms`；
- 最多返回 200 行、50 列和 1 MB JSON；
- 连接池与业务写连接池分离；
- 每次事务设置租户和项目范围上下文；
- 运行时账号没有业务表直接权限。

## 7. 审计

`analytics_query_audit` 保存用户问题摘要、目录版本、模型、生成 SQL、校验阶段、拒绝原因、执行耗时、返回行数、是否截断、request/trace ID 和用户标识。审计不保存完整返回数据。

## 8. 错误语义

| HTTP | 错误码 | 场景 |
| ---: | --- | --- |
| 403 | `ANALYTICS_NOT_PERMITTED` | 无角色或项目范围 |
| 422 | `SQL_GENERATION_INVALID` | 模型输出不是可解析 SELECT |
| 422 | `SQL_POLICY_VIOLATION` | 表、列、函数或语法不允许 |
| 422 | `SQL_COST_LIMIT_EXCEEDED` | 估算成本或行数超限 |
| 504 | `SQL_EXECUTION_TIMEOUT` | 超过 3 秒 |
| 503 | `ANALYTICS_UNAVAILABLE` | 只读数据源不可用 |

任何拒绝都返回审计 ID，不生成替代 SQL，不回退到业务写连接。

## 9. 测试与验收

- 合法查询覆盖计数、分组、时间范围、排序、受限 CTE 和允许 JOIN；
- 攻击集覆盖 INSERT/UPDATE/DELETE/DDL、COPY、系统表、危险函数、多语句、注释、递归 CTE、超大笛卡尔积和提示注入；
- Python 与 Java 校验器分别独立拦截同一攻击集；
- Testcontainers 证明 `analytics_reader` 无法直接读取或写业务表；
- 两租户相同问题得到各自数据，项目范围外行数为 0；
- 危险 SQL 拦截率 100%，跨租户泄漏为 0；
- 超时、成本、行数、列数和响应体限制均有真实数据库测试。
