# 多租户工单写入与操作确认 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Java 只读工单服务升级为 JWT 认证、租户与项目隔离、状态机约束、人工确认、幂等和可审计的工单命令服务，同时保持现有三个查询接口在租户内兼容。

**Architecture:** Spring Security 从 JWT 建立 `TenantContext`，应用层将令牌声明与数据库现行成员/项目范围求交；所有业务事务先通过 `set_config` 写入 PostgreSQL RLS 上下文。高风险写入先形成权威 `ActionProposal` 预览，非 AI 人员确认后由单一事务执行领域命令、审计、Assignment、Outbox 和幂等记录。Java 是工单事实唯一写入者。

**Tech Stack:** Java 17, Spring Boot 3.4.5, Spring Security OAuth2 Resource Server, MyBatis-Plus 3.5.12, Flyway, PostgreSQL 16 RLS, Testcontainers, JUnit 5, MockMvc.

## Global Constraints

- 只修改当前公开项目；任何仓库外私有参考资料始终只读，其标识和路径不得写入公开产物。
- 高风险命令（创建、派单、关键字段修改、状态变更）必须先建议、后人工确认；`AI_SERVICE` 只能创建建议。
- 任何租户表查询都必须同时依赖 RLS 和应用层项目授权；范围外资源统一表现为 404。
- 运行时账号不得拥有表、不得有 `BYPASSRLS`；Flyway 账号与 `work_order_app`、`ai_app`、`analytics_reader` 分离。
- 每个任务先写失败测试，再写最小实现；完成一个任务后只提交该任务列出的文件。

---

## File Structure Map

```text
infra/postgres/init/001_roles.sql                         # 开发库角色和授权边界
apps/work-order-service/src/main/resources/db/migration/
  V3__multitenant_work_order_schema.sql                   # 租户、身份、建议、审计、可靠性表
  V4__enable_tenant_rls.sql                               # FORCE RLS 与策略
  V5__split_synthetic_tenants.sql                         # 50 条数据迁移为两租户各 25 条
apps/work-order-service/src/main/java/com/tangmeng/workorder/
  security/{TenantContext,TenantContextResolver,SecurityConfig}.java
  tenant/{TenantAccessService,TenantTransaction}.java
  domain/{WorkOrderStatus,WorkOrderAction,WorkOrderStateMachine}.java
  command/{ActionProposalService,WorkOrderCommandService}.java
  command/model/{CreateProposalCommand,ConfirmProposalCommand}.java
  domain/{ActionProposalEntity,WorkOrderEventEntity}.java
  mapper/{ActionProposalMapper,WorkOrderEventMapper}.java
  api/{ActionProposalRequest,ActionProposalResponse,ConfirmProposalRequest}.java
  controller/ActionProposalController.java
apps/work-order-service/src/test/java/com/tangmeng/workorder/
  security/TenantContextResolverTest.java
  domain/WorkOrderStateMachineTest.java
  command/{ActionProposalServiceTest,WorkOrderCommandIntegrationTest}.java
  controller/{WorkOrderAuthorizationTest,ActionProposalControllerTest}.java
  integration/{TenantRlsIntegrationTest,IdempotencyConcurrencyTest}.java
```

## Task 1: Separate database roles and add security dependencies

**Files:**
- Create: `infra/postgres/init/001_roles.sql`
- Modify: `docker-compose.yml`
- Modify: `apps/work-order-service/pom.xml`
- Modify: `apps/work-order-service/src/main/resources/application.yml`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/integration/DatabaseRoleContractTest.java`

**Interfaces:**
- Produces JDBC identities `flyway_owner`, `work_order_app`, `ai_app`, `analytics_reader`.
- Produces JWT settings `security.jwt.issuer-uri` and `security.jwt.audience`.
- Consumes only synthetic development passwords from Compose environment; production values remain environment variables.

- [ ] **Step 1: Write the failing role contract test**

```java
@Test
void composeAndInitScriptDefineSeparatedRuntimeRoles() throws IOException {
    String init = Files.readString(Path.of("../../infra/postgres/init/001_roles.sql"));
    String compose = Files.readString(Path.of("../../docker-compose.yml"));
    assertThat(init).contains("flyway_owner", "work_order_app", "ai_app", "analytics_reader");
    assertThat(compose).contains("DB_USERNAME: work_order_app", "FLYWAY_USER: flyway_owner");
}
```

- [ ] **Step 2: Run RED**

```powershell
$env:JAVA_HOME='C:\Program Files\Zulu\zulu-17'
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=DatabaseRoleContractTest test
```

Expected: test fails because the init script and separated credentials do not exist.

- [ ] **Step 3: Add dependencies and exact role bootstrap**

Add `spring-boot-starter-security`, `spring-boot-starter-oauth2-resource-server`, and test-scope `spring-security-test`. The init script must create login roles only when absent, revoke `PUBLIC` schema/table privileges, and grant schema usage without granting tenant tables to `analytics_reader`. Configure Spring datasource as `work_order_app`; configure `spring.flyway.user/password` independently as `flyway_owner`.

- [ ] **Step 4: Run GREEN and commit**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=DatabaseRoleContractTest test
git add infra/postgres/init/001_roles.sql docker-compose.yml apps/work-order-service/pom.xml apps/work-order-service/src/main/resources/application.yml apps/work-order-service/src/test/java/com/tangmeng/workorder/integration/DatabaseRoleContractTest.java
git commit -m "build(db): separate work order database roles"
```

Expected: one passing contract test and a commit containing only role/configuration files.

## Task 2: Migrate the tenant-aware schema and synthetic data

**Files:**
- Create: `apps/work-order-service/src/main/resources/db/migration/V3__multitenant_work_order_schema.sql`
- Create: `apps/work-order-service/src/main/resources/db/migration/V4__enable_tenant_rls.sql`
- Create: `apps/work-order-service/src/main/resources/db/migration/V5__split_synthetic_tenants.sql`
- Modify: `apps/work-order-service/src/test/java/com/tangmeng/workorder/integration/MigrationContractTest.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/integration/TenantSchemaIntegrationTest.java`

**Interfaces:**
- Produces tables `tenant`, `user_identity`, `tenant_membership`, `project_scope`, `project`, `action_proposal`, `work_order_assignment`, `work_order_event`, `idempotency_record`, `outbox_event`, `inbox_message`.
- Changes `work_order` identity from `work_order_no` to UUID `id`, with unique `(tenant_id, work_order_no)`, optimistic `version`, and `accepted_at` for the separate acceptance command.
- Preserves exactly 50 synthetic work orders and all five rework chains.

- [ ] **Step 1: Write failing migration assertions**

```java
@Test
void migratedSeedHasTwoIsolatedTenants() {
    assertThat(jdbc.queryForObject("select count(*) from tenant", Long.class)).isEqualTo(2L);
    assertThat(jdbc.queryForList("select tenant_id, count(*) c from work_order group by tenant_id"))
        .extracting(row -> ((Number) row.get("c")).longValue())
        .containsExactlyInAnyOrder(25L, 25L);
}

@Test
void everyTenantTableCarriesTenantId() {
    for (String table : List.of("work_order", "action_proposal", "work_order_event",
            "work_order_assignment", "idempotency_record", "outbox_event", "inbox_message")) {
        Integer count = jdbc.queryForObject("select count(*) from information_schema.columns where table_name=? and column_name='tenant_id'", Integer.class, table);
        assertThat(count).isEqualTo(1);
    }
}
```

- [ ] **Step 2: Run RED**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=TenantSchemaIntegrationTest,MigrationContractTest test
```

Expected: missing tables and columns.

- [ ] **Step 3: Implement V3-V5**

Use fixed synthetic UUIDs for two tenants and three projects per tenant. Rebuild `work_order` into a new table so foreign keys use UUID IDs; map rows 1-25 to tenant A and 26-50 to tenant B. Add check constraints for statuses, proposal statuses and risk levels. All tenant-scoped unique constraints start with `tenant_id`. V4 must use this policy shape for every tenant table:

```sql
ALTER TABLE work_order ENABLE ROW LEVEL SECURITY;
ALTER TABLE work_order FORCE ROW LEVEL SECURITY;
CREATE POLICY work_order_tenant_policy ON work_order
USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
```

- [ ] **Step 4: Run GREEN and commit**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=TenantSchemaIntegrationTest,MigrationContractTest test
git add apps/work-order-service/src/main/resources/db/migration apps/work-order-service/src/test/java/com/tangmeng/workorder/integration
git commit -m "feat(db): add multitenant work order schema"
```

Expected: 50 rows, two groups of 25, and all schema tests pass.

## Task 3: Establish authenticated TenantContext and current database authorization

**Files:**
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/security/TenantContext.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/security/TenantContextResolver.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/security/SecurityConfig.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/tenant/TenantAccessService.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/tenant/TenantTransaction.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/security/TenantContextResolverTest.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/controller/WorkOrderAuthorizationTest.java`

**Interfaces:**
- Produces `TenantContext(UUID tenantId, UUID userId, String subject, Set<String> roles, Set<UUID> projectIds, String requestId, String traceId)`.
- `TenantAccessService.resolve(jwt)` intersects JWT claims with active `tenant_membership` and `project_scope` rows.
- `TenantTransaction.required(context, Supplier<T>)` sets `app.tenant_id` with transaction-local `set_config` before mapper access.

- [ ] **Step 1: Write failing claim/intersection tests**

```java
@Test
void intersectsTokenAndDatabaseAuthority() {
    Jwt jwt = Jwt.withTokenValue("test").header("alg", "none")
        .claim("sub", "dispatcher-1").claim("tenant_id", TENANT.toString())
        .claim("roles", List.of("DISPATCHER", "TENANT_ADMIN"))
        .claim("project_ids", List.of(PROJECT_A.toString(), PROJECT_B.toString()))
        .claim("scope", "work-order:write").build();
    when(access.loadCurrentRoles(TENANT, "dispatcher-1")).thenReturn(Set.of("DISPATCHER"));
    when(access.loadCurrentProjects(TENANT, "dispatcher-1")).thenReturn(Set.of(PROJECT_A));
    TenantContext context = resolver.resolve(jwt);
    assertThat(context.roles()).containsExactly("DISPATCHER");
    assertThat(context.projectIds()).containsExactly(PROJECT_A);
}
```

Add MockMvc cases: no token -> 401, inactive membership -> 403, inaccessible project/order -> 404.

- [ ] **Step 2: Run RED**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=TenantContextResolverTest,WorkOrderAuthorizationTest test
```

Expected: security/context classes are absent.

- [ ] **Step 3: Implement the security boundary**

`SecurityConfig` must validate issuer and audience, require authentication for `/api/**` and `/internal/**`, and allow only `/actuator/health`. The resolver rejects blank/malformed claims. Controller authorization is coarse; command services repeat role/project checks. `TenantTransaction` must execute:

```java
jdbcTemplate.queryForObject("select set_config('app.tenant_id', ?, true)", String.class, context.tenantId().toString());
```

inside a `TransactionTemplate`, never outside a transaction.

- [ ] **Step 4: Run GREEN and commit**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=TenantContextResolverTest,WorkOrderAuthorizationTest test
git add apps/work-order-service/pom.xml apps/work-order-service/src/main/java/com/tangmeng/workorder/security apps/work-order-service/src/main/java/com/tangmeng/workorder/tenant apps/work-order-service/src/test/java/com/tangmeng/workorder/security apps/work-order-service/src/test/java/com/tangmeng/workorder/controller/WorkOrderAuthorizationTest.java
git commit -m "feat(java): enforce tenant identity and authorization"
```

## Task 4: Encode the work-order state machine as a pure domain component

**Files:**
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/domain/WorkOrderStatus.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/domain/WorkOrderAction.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/domain/WorkOrderStateMachine.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/service/InvalidStateTransitionException.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/domain/WorkOrderStateMachineTest.java`

**Interfaces:**
- `WorkOrderStatus transition(WorkOrderSnapshot current, WorkOrderAction action)` returns the sole legal next state or throws `INVALID_STATE_TRANSITION`; `ACCEPT` records acceptance while retaining `PENDING_ACCEPTANCE`, and `START` requires `accepted_at` before entering `PROCESSING`.
- `void assertMutable(WorkOrderStatus status)` rejects `CLOSED` and `CANCELLED`.

- [ ] **Step 1: Write the full table-driven RED test**

```java
static Stream<Arguments> allowed() {
    return Stream.of(
        arguments(PENDING_DISPATCH, ASSIGN, PENDING_ACCEPTANCE),
        arguments(PENDING_ACCEPTANCE, ACCEPT, PENDING_ACCEPTANCE),
        arguments(PENDING_ACCEPTANCE, START, PROCESSING),
        arguments(PROCESSING, COMPLETE, COMPLETED),
        arguments(COMPLETED, CLOSE, CLOSED),
        arguments(PENDING_DISPATCH, CANCEL, CANCELLED),
        arguments(PENDING_ACCEPTANCE, CANCEL, CANCELLED),
        arguments(PROCESSING, CANCEL, CANCELLED),
        arguments(COMPLETED, CANCEL, CANCELLED));
}
```

The rejected test must enumerate every other status/action pair and assert error code `INVALID_STATE_TRANSITION`; add explicit tests that `ACCEPT` rejects an already populated `accepted_at`, `START` rejects a missing `accepted_at`, cancellation requires a nonblank reason, and terminal states are immutable.

- [ ] **Step 2: Run RED, implement enum-map transition, run GREEN**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=WorkOrderStateMachineTest test
```

Expected RED: missing types. Implement an immutable transition map containing exactly the nine cases above plus guards for acceptance metadata, then rerun expecting all parameterized cases to pass.

- [ ] **Step 3: Commit**

```powershell
git add apps/work-order-service/src/main/java/com/tangmeng/workorder/domain apps/work-order-service/src/main/java/com/tangmeng/workorder/service/InvalidStateTransitionException.java apps/work-order-service/src/test/java/com/tangmeng/workorder/domain
git commit -m "feat(java): define work order state machine"
```

## Task 5: Make all existing queries tenant and project scoped

**Files:**
- Modify: `apps/work-order-service/src/main/java/com/tangmeng/workorder/domain/WorkOrderEntity.java`
- Modify: `apps/work-order-service/src/main/java/com/tangmeng/workorder/api/WorkOrderResponse.java`
- Modify: `apps/work-order-service/src/main/java/com/tangmeng/workorder/service/WorkOrderQueryService.java`
- Modify: `apps/work-order-service/src/main/java/com/tangmeng/workorder/controller/WorkOrderController.java`
- Modify: `apps/work-order-service/src/test/java/com/tangmeng/workorder/service/WorkOrderQueryServiceTest.java`
- Modify: `apps/work-order-service/src/test/java/com/tangmeng/workorder/controller/WorkOrderControllerTest.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/integration/TenantRlsIntegrationTest.java`

**Interfaces:**
- Query signatures become `get(TenantContext, String)`, `search(TenantContext, criteria, page, size)`, and `reworkChain(TenantContext, String)`.
- Response adds `id`, `project_id`, `assignee_id`, and `version` without removing existing JSON fields.

- [ ] **Step 1: Add RED tests for two-tenant detail, page totals and rework chains**

Use two JWTs and assert tenant A cannot fetch tenant B's number, cannot see its page count, and cannot include it in a rework chain. Test missing `app.tenant_id` with direct runtime-role JDBC and assert zero visible rows or an authorization error.

- [ ] **Step 2: Run RED**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=WorkOrderQueryServiceTest,WorkOrderControllerTest,TenantRlsIntegrationTest test
```

- [ ] **Step 3: Implement scoped queries**

Every mapper predicate must include `tenantId` and `projectId IN context.projectIds()` in addition to RLS. Stop using `selectById(workOrderNo)`; query `(tenant_id, work_order_no)`. Resolve the root by UUID and tenant before loading its chain.

- [ ] **Step 4: Run GREEN and commit**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=WorkOrderQueryServiceTest,WorkOrderControllerTest,TenantRlsIntegrationTest test
git add apps/work-order-service/src/main/java apps/work-order-service/src/test/java
git commit -m "feat(java): isolate work order read APIs by tenant"
```

## Task 6: Create authoritative action proposals and previews

**Files:**
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/domain/ActionProposalEntity.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/mapper/ActionProposalMapper.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/command/model/CreateProposalCommand.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/command/ActionProposalService.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/api/ActionProposalRequest.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/api/ActionProposalResponse.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/controller/ActionProposalController.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/command/ActionProposalServiceTest.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/controller/ActionProposalControllerTest.java`

**Interfaces:**
- `POST /api/action-proposals` accepts `action_type`, optional `target_work_order_no`, and action-specific `parameters`.
- Server returns UUID, risk/status, authoritative before/after snapshots, expected version, and expiry.
- Client-supplied tenant, snapshots, risk, status, expected version, requester, confirmer and result fields are rejected as unknown JSON.
- Effective roles are exact constants `TENANT_ADMIN`, `DISPATCHER`, `OPERATOR`, `QUALITY_REVIEWER`, and `AI_SERVICE`; `QUALITY_REVIEWER` owns close and rectification confirmation.

- [ ] **Step 1: Write RED contract tests**

Test `CREATE`, `ASSIGN`, `UPDATE`, `ACCEPT`, `START`, `COMPLETE`, `CLOSE`, `CANCEL`; assert preview comes from the database, expires in 15 minutes, and `AI_SERVICE` may create but not alter authority fields. Assert invalid fields return `422 INVALID_COMMAND`.

- [ ] **Step 2: Run RED**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=ActionProposalServiceTest,ActionProposalControllerTest test
```

- [ ] **Step 3: Implement proposal generation**

Use a sealed command hierarchy and Jackson `JsonNode` snapshots. `CREATE` has no before snapshot and expected version 0; target commands reload the current row inside tenant transaction. The after snapshot is a preview only and never updates `work_order`. Role checks: dispatcher for create/assign/update/cancel; operator for accept/start/complete on self-assigned order; quality reviewer for close.

- [ ] **Step 4: Run GREEN and commit**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=ActionProposalServiceTest,ActionProposalControllerTest test
git add apps/work-order-service/src/main/java/com/tangmeng/workorder apps/work-order-service/src/test/java/com/tangmeng/workorder
git commit -m "feat(java): create authoritative action proposals"
```

## Task 7: Confirm proposals with optimistic locking, audit, outbox and idempotency

**Files:**
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/command/WorkOrderCommandService.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/api/ConfirmProposalRequest.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/domain/WorkOrderEventEntity.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/mapper/WorkOrderEventMapper.java`
- Modify: `apps/work-order-service/src/main/java/com/tangmeng/workorder/command/ActionProposalService.java`
- Modify: `apps/work-order-service/src/main/java/com/tangmeng/workorder/controller/ActionProposalController.java`
- Modify: `apps/work-order-service/src/main/java/com/tangmeng/workorder/controller/GlobalExceptionHandler.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/command/WorkOrderCommandIntegrationTest.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/integration/IdempotencyConcurrencyTest.java`

**Interfaces:**
- `POST /api/action-proposals/{id}/confirm` requires `Idempotency-Key`; `POST .../reject` records the human decision.
- `WorkOrderCommandService.execute(context, proposal, idempotencyKey)` returns a stable execution response and performs no nested autonomous confirmation.
- Error codes: `ACTION_NOT_PERMITTED`, `WORK_ORDER_NOT_FOUND`, `WORK_ORDER_VERSION_CONFLICT`, `INVALID_STATE_TRANSITION`, `ACTION_PROPOSAL_EXPIRED`, `INVALID_COMMAND`.

- [ ] **Step 1: Write RED integration tests**

Cover one successful command of every action type, exact version increment, Assignment interval change, immutable event, Outbox row, same-key replay, same-key/different-payload conflict, expired proposal, `AI_SERVICE` confirmation denial, rollback after forced event insert failure, stale version and two simultaneous confirmations.

- [ ] **Step 2: Run RED**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=WorkOrderCommandIntegrationTest,IdempotencyConcurrencyTest test
```

- [ ] **Step 3: Implement the single transaction in this exact order**

```text
load/replay idempotency -> claim proposal CONFIRMED to EXECUTING
-> update/insert work_order with tenant + version predicate
-> append assignment when assignee changes
-> append work_order_event -> append outbox_event
-> store idempotent response -> mark proposal EXECUTED
```

Claim pending proposals atomically while confirming; reject expired requests with 410. Any execution failure rolls back the command transaction, then a separate recovery transaction marks the proposal `FAILED`. On version conflict, set `WORK_ORDER_VERSION_CONFLICT` and return a freshly computed preview without altering the work order. Hash `(operation, canonical request body)` in the idempotency record.

- [ ] **Step 4: Run GREEN and commit**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=WorkOrderCommandIntegrationTest,IdempotencyConcurrencyTest test
git add apps/work-order-service/src/main/java apps/work-order-service/src/test/java
git commit -m "feat(java): execute confirmed work order commands"
```

Expected: concurrent confirmation produces one `work_order_event`, one `outbox_event`, and the same response for idempotent replay.

## Task 8: Complete phase acceptance and documentation

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `scripts/smoke_test.py`
- Create: `docs/api/action-proposals.md`

**Interfaces:**
- Documents local test JWT claims, proposal lifecycle, role matrix, errors and copyable create/confirm examples.
- Smoke test proves authenticated read, proposal preview, human confirmation and idempotent replay.

- [ ] **Step 1: Extend smoke tests before documentation**

Add assertions that an unauthenticated request is 401, tenant A cannot see tenant B, an `AI_SERVICE` confirmation is 403, and one dispatcher proposal confirmation changes exactly one version.

- [ ] **Step 2: Run the full phase suite**

```powershell
$env:JAVA_HOME='C:\Program Files\Zulu\zulu-17'
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml test
docker compose up --build -d
python scripts/smoke_test.py
docker compose down
git diff --check
```

Expected: all Java tests pass, Docker smoke prints `smoke tests: PASS`, and `git diff --check` is silent.

- [ ] **Step 3: Commit verified phase documentation**

```powershell
git add .env.example README.md docs/architecture.md docs/api/action-proposals.md scripts/smoke_test.py
git commit -m "docs: document multitenant command workflow"
git status --short --branch
```

Expected: clean worktree, local branch ahead of remote, no push performed.
