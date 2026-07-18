# 安全 NL2SQL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `ANALYST` 提供自然语言分析端点，只能针对三个脱敏分析视图执行租户/项目隔离的单条只读 SELECT，并以 Python 和 Java 两个独立 AST 校验器、成本门槛和专用只读账号防御模型生成 SQL。

**Architecture:** Python 维护不可被知识内容改变的版本化语义目录，调用模型生成 SQL，用 sqlglot 做第一层解析和规范化；Java 内部执行端点用 JSQLParser 独立重验、EXPLAIN 成本评估和独立 `analytics_reader` 连接池执行。PostgreSQL `security_invoker` 视图依赖底层 RLS，事务设置租户和项目数组上下文。

**Tech Stack:** Python 3.12/FastAPI/sqlglot/Pydantic/httpx, Java 17/Spring Boot/JSQLParser/JdbcClient, PostgreSQL 16 security-invoker views/RLS, Testcontainers, pytest/JUnit.

## Global Constraints

- 依赖阶段 1 身份/租户与阶段 3 质检整改表；端点只允许 `ANALYST`。
- 用户请求只包含自然语言问题，不能上传 SQL、表名、连接参数或租户 ID。
- Python 与 Java 都必须从原始 SQL 独立解析；Java 不接受 Python 的“已验证”标志。
- 任何拒绝都记录并返回 `audit_id`；不得使用业务写连接回退。
- SQL 与结构化行集必须随回答返回；自然语言解释不得添加行集中不存在的事实。

---

## File Structure Map

```text
apps/work-order-service/src/main/resources/db/migration/
  V7__analytics_views.sql
  V8__analytics_audit_and_grants.sql
apps/work-order-service/src/main/java/com/tangmeng/workorder/analytics/
  AnalyticsDataSourceConfig.java AnalyticsSqlPolicy.java AnalyticsCostGuard.java
  AnalyticsExecutor.java AnalyticsController.java AnalyticsAuditService.java
apps/work-order-service/src/test/java/com/tangmeng/workorder/analytics/
  AnalyticsSqlPolicyTest.java AnalyticsControllerTest.java AnalyticsIntegrationTest.java
apps/ai-service/app/analytics/
  catalog.py models.py sql_policy.py planner.py client.py service.py router.py
apps/ai-service/tests/analytics/
  test_catalog.py test_sql_policy.py test_planner.py test_api.py test_attack_corpus.py
eval/nl2sql_attack_cases.json
eval/nl2sql_questions.json
```

## Task 1: Create security-invoker analytics views and grants

**Files:**
- Create: `apps/work-order-service/src/main/resources/db/migration/V7__analytics_views.sql`
- Create: `apps/work-order-service/src/main/resources/db/migration/V8__analytics_audit_and_grants.sql`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/analytics/AnalyticsViewIntegrationTest.java`

**Interfaces:**
- Produces `analytics_work_order_v`, `analytics_quality_v`, `analytics_rectification_v` and `analytics_query_audit`.
- Every view includes `tenant_id` and `project_id` internally for RLS but the public catalog does not permit selecting `tenant_id`.
- `analytics_reader` gets SELECT only on the views and INSERT only through an audited definer-free service path; it gets no base-table grant.

- [ ] **Step 1: Write RED view/grant tests**

```java
@Test
void analyticsReaderCannotReadOrWriteBaseTables() {
    assertThatThrownBy(() -> analyticsJdbc.queryForList("select * from work_order"))
        .hasMessageContaining("permission denied");
    assertThatThrownBy(() -> analyticsJdbc.update("delete from work_order"))
        .hasMessageContaining("permission denied");
}
```

Add metadata assertions for `security_invoker=true`; assert disallowed columns (description, contact, attachment URL, token, raw model response) do not exist; assert missing tenant/project settings expose zero rows.

- [ ] **Step 2: Run RED**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=AnalyticsViewIntegrationTest test
```

- [ ] **Step 3: Implement exact views and project-aware RLS**

Create views only from approved columns. Extend underlying policies used by analytics to require:

```sql
tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
AND project_id = ANY(
  coalesce(nullif(current_setting('app.project_ids', true), '')::uuid[], ARRAY[]::uuid[])
)
```

For quality rows, reach project through the tenant-scoped work-order key. `analytics_query_audit` stores summaries and metadata, never returned row values.

- [ ] **Step 4: Run GREEN and commit**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=AnalyticsViewIntegrationTest test
git add apps/work-order-service/src/main/resources/db/migration/V7__analytics_views.sql apps/work-order-service/src/main/resources/db/migration/V8__analytics_audit_and_grants.sql apps/work-order-service/src/test/java/com/tangmeng/workorder/analytics/AnalyticsViewIntegrationTest.java
git commit -m "feat(db): expose isolated analytics views"
```

## Task 2: Build a versioned, code-owned semantic catalog

**Files:**
- Modify: `apps/ai-service/pyproject.toml`
- Create: `apps/ai-service/app/analytics/catalog.py`
- Create: `apps/ai-service/app/analytics/models.py`
- Test: `apps/ai-service/tests/analytics/test_catalog.py`

**Interfaces:**
- `CATALOG_VERSION = "2026-07-18.1"`.
- Catalog exposes exactly three views, their allowed columns/types/enums, approved functions, and approved joins.
- `render_prompt_catalog()` is generated only from frozen Python structures.

- [ ] **Step 1: Write RED catalog tests**

Assert exact view set, unique Chinese synonyms, no sensitive columns, only joins `work_order.project_id = quality.project_id` and `work_order.project_id = rectification.project_id` with tenant alignment implicit, and stable SHA-256 serialization.

- [ ] **Step 2: Run RED, add `sqlglot>=26,<27`, implement frozen models, run GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/analytics/test_catalog.py -q
```

The catalog must not read PostgreSQL comments, Markdown policies, environment variables, user text or model output.

- [ ] **Step 3: Commit**

```powershell
git add apps/ai-service/pyproject.toml apps/ai-service/app/analytics apps/ai-service/tests/analytics/test_catalog.py
git commit -m "feat(ai): define analytics semantic catalog"
```

## Task 3: Implement Python SQL AST policy and normalization

**Files:**
- Create: `apps/ai-service/app/analytics/sql_policy.py`
- Test: `apps/ai-service/tests/analytics/test_sql_policy.py`
- Create: `eval/nl2sql_attack_cases.json`

**Interfaces:**
- `SqlPolicy.validate_and_normalize(sql: str) -> ValidatedSql` returns canonical PostgreSQL SQL with effective limit at most 200.
- Throws stable `SQL_GENERATION_INVALID` for parse/root/count failures and `SQL_POLICY_VIOLATION` for forbidden constructs/identifiers.

- [ ] **Step 1: Write RED allow/deny corpus tests**

Allowed: count, group, time filter, order, nonrecursive CTE, approved inner/left joins, CASE, date truncation and limits. Denied: DML/DDL, COPY, system schemas, base tables, dangerous/file/network functions, `SELECT *`, comments, semicolon multi-statements, recursive/materialized CTE, lateral/cross joins, locks, set operations not in v1, identifier construction and unknown columns.

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/analytics/test_sql_policy.py -q
```

- [ ] **Step 3: Implement AST traversal**

Parse with `sqlglot.parse(sql, read="postgres")`, require one expression and a SELECT/CTE root, resolve aliases to catalog relations, validate every column/function/join and reject comments before parsing. Add `LIMIT 200` when absent and replace larger literal limits with 200. Reject parameter markers and nonliteral LIMIT/OFFSET.

- [ ] **Step 4: Run GREEN and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/analytics/test_sql_policy.py -q
git add apps/ai-service/app/analytics/sql_policy.py apps/ai-service/tests/analytics/test_sql_policy.py eval/nl2sql_attack_cases.json
git commit -m "feat(ai): validate generated analytics SQL"
```

## Task 4: Generate SQL from natural language with audited failure semantics

**Files:**
- Create: `apps/ai-service/app/analytics/planner.py`
- Modify: `apps/ai-service/app/llm/contracts.py`
- Test: `apps/ai-service/tests/analytics/test_planner.py`

**Interfaces:**
- `AnalyticsPlanner.plan(question, tenant_context) -> PlannedQuery(sql, catalog_version, model_metadata)`.
- Model structured response is exactly `{ "sql": "SELECT ..." }`; unknown keys fail.
- Planner never asks the model to decide tenant or project predicates; database context enforces them.

- [ ] **Step 1: Write RED model matrix**

Test valid SQL, Markdown fences, prose around SQL, empty SQL, bad JSON, multiple statements, prompt injection asking for raw/base/system tables, model timeout and provider auth failure. Any invalid output returns one error and never invents a replacement query.

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/analytics/test_planner.py -q
```

- [ ] **Step 3: Implement strict planning**

System prompt contains the frozen catalog, supported query grammar, instruction to emit JSON only and no data values beyond enumerations. Parse with a Pydantic model `extra="forbid"`, then call `SqlPolicy`. Record provider/model/prompt catalog version and latency, not hidden reasoning.

- [ ] **Step 4: Run GREEN and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/analytics/test_planner.py -q
git add apps/ai-service/app/analytics/planner.py apps/ai-service/app/llm apps/ai-service/tests/analytics/test_planner.py
git commit -m "feat(ai): plan catalog constrained analytics SQL"
```

## Task 5: Implement independent Java AST validation

**Files:**
- Modify: `apps/work-order-service/pom.xml`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/analytics/AnalyticsSqlPolicy.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/analytics/AnalyticsPolicyException.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/analytics/AnalyticsSqlPolicyTest.java`

**Interfaces:**
- `ValidatedAnalyticsSql validate(String rawSql, String catalogVersion)` reparses with JSQLParser and rejects unknown catalog versions.
- Java allowlist is separately defined in Java constants; it does not load Python output or a Python-generated approval list.

- [ ] **Step 1: Parameterize RED tests from the shared JSON attack corpus**

Copy only the neutral attack cases into Java test resources and assert every malicious SQL is rejected even if Python were bypassed. Add the legal query set and exact LIMIT enforcement.

- [ ] **Step 2: Run RED**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=AnalyticsSqlPolicyTest test
```

- [ ] **Step 3: Add JSQLParser and implement visitor checks**

Pin `com.github.jsqlparser:jsqlparser` to one tested version. Require a single `PlainSelect` or approved nonrecursive `WithItem` tree; walk tables, columns, functions, joins, locks and limits. Reject parser recovery, unsupported statements and any object not in the Java allowlist. Normalize limit to 200 independently.

- [ ] **Step 4: Run GREEN and commit**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=AnalyticsSqlPolicyTest test
git add apps/work-order-service/pom.xml apps/work-order-service/src/main/java/com/tangmeng/workorder/analytics apps/work-order-service/src/test/java/com/tangmeng/workorder/analytics apps/work-order-service/src/test/resources
git commit -m "feat(java): independently validate analytics SQL"
```

## Task 6: Execute through a bounded read-only datasource

**Files:**
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/analytics/AnalyticsDataSourceConfig.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/analytics/AnalyticsCostGuard.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/analytics/AnalyticsExecutor.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/analytics/AnalyticsAuditService.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/analytics/AnalyticsIntegrationTest.java`

**Interfaces:**
- Separate Hikari pool uses `analytics_reader` and max pool size 3.
- Execution returns at most 200 rows, 50 columns and 1 MiB encoded JSON, plus truncated flag.
- Cost guard rejects total cost >100000 or plan rows >1000000.

- [ ] **Step 1: Write RED real-database boundary tests**

Test read-only transaction, 3-second statement timeout, 500-ms lock timeout, cost rejection, row/column/body truncation, base-table denial, missing tenant/project context, two tenants/scope subsets, and unavailable analytics datasource. Verify every outcome inserts an audit row without full result data.

- [ ] **Step 2: Run RED**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=AnalyticsIntegrationTest test
```

- [ ] **Step 3: Implement transaction setup and EXPLAIN**

Execute in this order on the analytics connection: begin read-only; `set local statement_timeout='3000ms'`; `set local lock_timeout='500ms'`; set `app.tenant_id`; set `app.project_ids` to PostgreSQL UUID array text; run `EXPLAIN (FORMAT JSON)`; enforce cost; run validated SQL; stream bounded cells while counting encoded UTF-8 bytes. Never interpolate tenant/project values into generated SQL.

- [ ] **Step 4: Run GREEN and commit**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=AnalyticsIntegrationTest test
git add apps/work-order-service/src/main/java/com/tangmeng/workorder/analytics apps/work-order-service/src/test/java/com/tangmeng/workorder/analytics apps/work-order-service/src/main/resources/application.yml
git commit -m "feat(java): execute bounded read only analytics"
```

## Task 7: Wire protected Java internal API and public FastAPI endpoint

**Files:**
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/analytics/AnalyticsController.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/analytics/AnalyticsExecuteRequest.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/analytics/AnalyticsExecuteResponse.java`
- Create: `apps/ai-service/app/analytics/client.py`
- Create: `apps/ai-service/app/analytics/service.py`
- Create: `apps/ai-service/app/analytics/router.py`
- Modify: `apps/ai-service/app/main.py`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/analytics/AnalyticsControllerTest.java`
- Test: `apps/ai-service/tests/analytics/test_api.py`

**Interfaces:**
- Java: `POST /internal/analytics/execute`, service scope `analytics:execute`, propagated tenant/subject/project/request/trace context.
- Python: `POST /analytics/query`, request `{question}`, response `{answer,sql,columns,rows,truncated,audit_id,latency_ms}`.
- Public caller must have effective `ANALYST` role and nonempty project scope.

- [ ] **Step 1: Write RED API/error tests**

Cover 401, 403, empty project scope, valid query, all six stable errors, audit ID on every rejection, internal service identity failure, context mismatch, client timeout and deterministic explanation from rows. Assert the answer mentions no number/value absent from serialized rows.

- [ ] **Step 2: Run RED**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=AnalyticsControllerTest test
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/analytics/test_api.py -q
```

- [ ] **Step 3: Implement endpoint flow**

Python authenticates user, plans and validates SQL, then calls Java with a service token plus signed user-context headers. Java derives tenant/project from verified delegation claims, checks catalog version and AST, executes, audits and returns rows. Python constructs a deterministic table summary first; optional model explanation receives only columns/rows and cannot alter SQL or audit metadata.

- [ ] **Step 4: Run GREEN and commit**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=AnalyticsControllerTest test
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/analytics/test_api.py -q
git add apps/work-order-service/src/main/java/com/tangmeng/workorder/analytics apps/work-order-service/src/test/java/com/tangmeng/workorder/analytics apps/ai-service/app/analytics apps/ai-service/app/main.py apps/ai-service/tests/analytics/test_api.py
git commit -m "feat(analytics): expose secure natural language queries"
```

## Task 8: Run attack, isolation and quality acceptance

**Files:**
- Create: `eval/nl2sql_questions.json`
- Create: `eval/run_nl2sql_eval.py`
- Modify: `scripts/smoke_test.py`
- Modify: `README.md`
- Create: `docs/api/analytics.md`
- Modify: `docs/architecture.md`
- Test: `apps/ai-service/tests/analytics/test_attack_corpus.py`

- [ ] **Step 1: Create the checked-in evaluation suites**

Include legal count/group/time/order/CTE/join questions and attacks for DML, DDL, COPY, system tables, dangerous functions, multiple statements, comments, recursive CTE, Cartesian explosion and prompt injection. Test the same SQL attack strings against Python and Java validators independently.

- [ ] **Step 2: Run full verification**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml test
.\.venv\Scripts\python.exe -m ruff check apps/ai-service
.\.venv\Scripts\python.exe -m mypy apps/ai-service/app
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests -q
docker compose up --build -d
python eval/run_nl2sql_eval.py --base-url http://localhost:8000
python scripts/smoke_test.py --scenario analytics
docker compose down
git diff --check
```

Expected: dangerous SQL blocked 100%; zero cross-tenant/project rows; all real cost/timeout/row/column/body guards pass; every response or rejection has an audit ID.

- [ ] **Step 3: Commit verified phase**

```powershell
git add eval scripts/smoke_test.py README.md docs
git commit -m "test(analytics): verify NL2SQL safety boundaries"
git status --short --branch
```

Expected: clean worktree and no push.
