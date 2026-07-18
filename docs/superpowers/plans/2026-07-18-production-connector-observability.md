# 生产连接器与可观测性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不包含任何私有系统细节的前提下，为工单事实源增加可替换的 Local/通用 HTTP 连接器、未知结果对账、Webhook/轮询同步、端到端追踪与指标，以及可验证的公开仓库隐私门禁。

**Architecture:** Java 领域命令只依赖 `WorkOrderConnector` 端口。Local 适配器复用现有 PostgreSQL 领域实现；HTTP 适配器使用服务身份调用通用上游，外部系统成为事实源，本地表降为只读投影。超时结果进入 `UNKNOWN`，必须按幂等键查询后才能完成或重试。同步事件经 Inbox 去重，OpenTelemetry 上下文贯穿 Python、Java、数据库和 Mock 上游。

**Tech Stack:** Java 17/Spring Boot HTTP Interface/Resilience4j/WireMock/OpenTelemetry/Micrometer, Python OpenTelemetry/FastAPI/httpx, PostgreSQL/Flyway, Prometheus, GitHub Actions/PowerShell privacy scan.

## Global Constraints

- 本计划只提供通用端口、通用 `/v1/work-orders` 示例适配器和 Mock 契约；真实企业映射必须在仓库外。
- HTTP 模式外部系统是事实源，本地 `work_order` 只能由同步/对账适配器更新投影。
- `UNKNOWN` 禁止直接重放写请求；必须先 `findByIdempotencyKey`。
- 用户 Token 不透传上游；认证 Secret 不进入日志、Span、指标标签或异常文本。
- 私有参考项目全程未修改、未暂存、未提交、未推送，并且公开扫描规则不得包含其真实标识。

---

## File Structure Map

```text
apps/work-order-service/src/main/resources/db/migration/V9__connector_sync.sql
apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/
  WorkOrderConnector.java ConnectorResult.java ConnectorException.java
  local/LocalWorkOrderConnector.java
  http/{HttpWorkOrderConnector,HttpConnectorClient,HttpConnectorProperties}.java
  sync/{WebhookController,WebhookVerifier,PollingSynchronizer,ReconciliationService}.java
apps/work-order-service/src/test/java/com/tangmeng/workorder/connector/
  WorkOrderConnectorContract.java LocalWorkOrderConnectorTest.java
  HttpWorkOrderConnectorTest.java SyncIntegrationTest.java ReconciliationIntegrationTest.java
apps/ai-service/app/observability.py
apps/work-order-service/src/main/java/com/tangmeng/workorder/observability/TelemetryConfig.java
infra/otel/otel-collector-config.yaml
infra/prometheus/prometheus.yml
scripts/privacy_scan.ps1
.github/workflows/ci.yml
```

## Task 1: Define the connector port and run one shared contract suite

**Files:**
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/WorkOrderConnector.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/ConnectorResult.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/ConnectorException.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/ConnectorWorkOrder.java`
- Create: `apps/work-order-service/src/test/java/com/tangmeng/workorder/connector/WorkOrderConnectorContract.java`

**Interfaces:**
- Exact methods: `get`, `search`, `create`, `assign`, `update`, `transition`, `findByIdempotencyKey` with `TenantContext` first.
- Write results are `CONFIRMED`, `REJECTED` or `UNKNOWN`; stable errors do not expose upstream bodies.
- DTO includes stable internal fields only; external payload types remain inside adapter package.

- [ ] **Step 1: Write the RED abstract contract**

```java
interface WorkOrderConnector {
    ConnectorWorkOrder get(TenantContext context, UUID workOrderId);
    ConnectorPage search(TenantContext context, WorkOrderSearchCriteria criteria, PageRequest page);
    ConnectorResult create(TenantContext context, CreateWorkOrderCommand command, String idempotencyKey);
    ConnectorResult assign(TenantContext context, UUID id, AssignWorkOrderCommand command, String idempotencyKey);
    ConnectorResult update(TenantContext context, UUID id, UpdateWorkOrderCommand command, long expectedVersion, String idempotencyKey);
    ConnectorResult transition(TenantContext context, UUID id, TransitionWorkOrderCommand command, long expectedVersion, String idempotencyKey);
    Optional<ConnectorResult> findByIdempotencyKey(TenantContext context, String operation, String idempotencyKey);
}
```

The contract suite tests CRUD semantics allowed by confirmed proposals, tenant/project scope, optimistic conflict, stable errors and idempotent replay against a factory supplied by each adapter.

- [ ] **Step 2: Run RED**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=WorkOrderConnectorContract test
```

- [ ] **Step 3: Implement only port/DTO/error types, run compile GREEN and commit**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -DskipTests compile
git add apps/work-order-service/src/main/java/com/tangmeng/workorder/connector apps/work-order-service/src/test/java/com/tangmeng/workorder/connector/WorkOrderConnectorContract.java
git commit -m "refactor(java): define work order connector port"
```

## Task 2: Adapt the local domain implementation behind the port

**Files:**
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/local/LocalWorkOrderConnector.java`
- Modify: `apps/work-order-service/src/main/java/com/tangmeng/workorder/command/WorkOrderCommandService.java`
- Modify: `apps/work-order-service/src/main/java/com/tangmeng/workorder/service/WorkOrderQueryService.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/ConnectorConfig.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/connector/LocalWorkOrderConnectorTest.java`

**Interfaces:**
- `connector.mode=local|http`, default `local`; invalid mode fails startup.
- Local adapter is the only component that calls the existing mapper/domain transaction implementation in local mode.

- [ ] **Step 1: Instantiate the shared contract with Local adapter and run RED**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=LocalWorkOrderConnectorTest test
```

- [ ] **Step 2: Move local reads/writes behind the adapter without changing behavior**

Keep proposal confirmation, RLS, state machine, idempotency, audit and Outbox semantics. The command service delegates the final fact-source operation to the connector but still owns proposal status and local audit. Add conditional beans and fail when zero or two connectors exist.

- [ ] **Step 3: Run all phase-1 compatibility tests and commit**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml test
git add apps/work-order-service/src/main/java/com/tangmeng/workorder apps/work-order-service/src/test/java/com/tangmeng/workorder/connector/LocalWorkOrderConnectorTest.java
git commit -m "refactor(java): route local facts through connector"
```

Expected: all existing Java tests remain green under default local mode.

## Task 3: Implement a generic HTTP adapter with safe identity propagation

**Files:**
- Modify: `apps/work-order-service/pom.xml`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/http/HttpConnectorProperties.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/http/HttpConnectorClient.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/http/HttpWorkOrderConnector.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/connector/HttpWorkOrderConnectorTest.java`

**Interfaces:**
- Generic paths: `GET/POST /v1/work-orders`, `GET/PATCH /v1/work-orders/{id}`, `POST /v1/work-orders/{id}/assign`, `POST /v1/work-orders/{id}/transitions`, `GET /v1/idempotency/{operation}/{key}`.
- Auth modes: OAuth2 client credentials, static bearer secret, mTLS; user token is never forwarded.
- Audit headers: `X-Tenant-Id`, `X-Actor-Subject`, `X-Request-Id`, W3C `traceparent`, plus `Idempotency-Key` on writes.

- [ ] **Step 1: Run the shared connector contract against WireMock (RED)**

Add cases for success, same-key 409 lookup, business 400/404/409, 429, 502, 503, timeout, connection reset, malformed JSON, wrong tenant echo and secret redaction. Inspect every request and prove the incoming user Authorization value is absent.

- [ ] **Step 2: Run RED**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=HttpWorkOrderConnectorTest test
```

- [ ] **Step 3: Implement bounded HTTP behavior**

Use connect timeout 1s and response timeout 5s. 2xx maps to `CONFIRMED`; non-idempotent 4xx maps to stable `REJECTED`; 409 with the same key triggers lookup; 429/502/503 retries only when upstream explicitly advertises idempotency and maximum attempts is 2; timeout/reset maps to `UNKNOWN`. Exception messages contain method, generic route template, status and stable code, never response body or credentials.

- [ ] **Step 4: Run GREEN and commit**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=HttpWorkOrderConnectorTest test
git add apps/work-order-service/pom.xml apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/http apps/work-order-service/src/test/java/com/tangmeng/workorder/connector/HttpWorkOrderConnectorTest.java
git commit -m "feat(connector): add generic HTTP work order adapter"
```

## Task 4: Persist UNKNOWN operations and reconcile before any retry

**Files:**
- Create: `apps/work-order-service/src/main/resources/db/migration/V9__connector_sync.sql`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/sync/ConnectorOperationEntity.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/sync/ReconciliationService.java`
- Modify: `apps/work-order-service/src/main/java/com/tangmeng/workorder/command/WorkOrderCommandService.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/connector/ReconciliationIntegrationTest.java`

**Interfaces:**
- Migration adds `connector_operation`, `sync_cursor`, and projection version/external ID columns with FORCE RLS.
- UNKNOWN proposal remains executing with `execution_result.status=UNKNOWN`; reconciliation searches by original operation/key.
- Recovered success updates projection, audit, Outbox and proposal result once; confirmed absence may move to controlled retry only when policy permits.

- [ ] **Step 1: Write RED unknown-outcome tests**

Simulate upstream applying a write then timing out; assert the service does not POST again, lookup finds the result, and exactly one local event/projection change appears. Also test lookup unavailable, explicit not-found, conflicting upstream payload and two reconcilers claiming one operation.

- [ ] **Step 2: Run RED**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=ReconciliationIntegrationTest test
```

- [ ] **Step 3: Implement state transitions**

Use `PENDING_RECONCILIATION -> RECONCILING -> RECOVERED|CONFIRMED_ABSENT|FAILED`, CAS/lease and exponential polling capped at 60 minutes. A retry POST requires `CONFIRMED_ABSENT`, the same idempotency key and connector capability `safeReplay=true`; otherwise stop and alert.

- [ ] **Step 4: Run GREEN and commit**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=ReconciliationIntegrationTest test
git add apps/work-order-service/src/main/resources/db/migration/V9__connector_sync.sql apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/sync apps/work-order-service/src/main/java/com/tangmeng/workorder/command/WorkOrderCommandService.java apps/work-order-service/src/test/java/com/tangmeng/workorder/connector/ReconciliationIntegrationTest.java
git commit -m "feat(connector): reconcile unknown write outcomes"
```

## Task 5: Add signed Webhook, polling sync and daily drift detection

**Files:**
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/sync/WebhookController.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/sync/WebhookVerifier.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/sync/PollingSynchronizer.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/sync/ReconciliationScheduler.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/connector/SyncIntegrationTest.java`

**Interfaces:**
- `POST /internal/connectors/{connectorKey}/webhooks` validates HMAC timestamp/signature before parsing payload.
- Polling uses per-tenant cursor and generic `updated_since` pagination.
- Projection updates require monotonically increasing external version; older/equal events are safely ignored through Inbox uniqueness.

- [ ] **Step 1: Write RED sync tests**

Cover valid signature, bad signature, timestamp outside five minutes, replayed message ID, out-of-order version, paging/cursor commit, per-tenant credentials/cursors, poll failure before cursor commit, and daily mismatches in version/status/assignee generating an alert task rather than silent overwrite.

- [ ] **Step 2: Run RED, implement, run GREEN**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=SyncIntegrationTest test
```

Verify the raw signature against bounded raw bytes first; cap payload at 1 MiB; store message hash and generic metadata, not secrets. Poll one tenant per transaction and update cursor only after all page rows commit.

- [ ] **Step 3: Commit**

```powershell
git add apps/work-order-service/src/main/java/com/tangmeng/workorder/connector/sync apps/work-order-service/src/test/java/com/tangmeng/workorder/connector/SyncIntegrationTest.java
git commit -m "feat(connector): synchronize external work order projections"
```

## Task 6: Add end-to-end tracing with a strict attribute allowlist

**Files:**
- Modify: `apps/work-order-service/pom.xml`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/observability/TelemetryConfig.java`
- Modify: `apps/ai-service/pyproject.toml`
- Create: `apps/ai-service/app/observability.py`
- Modify: `apps/ai-service/app/main.py`
- Modify: `apps/ai-service/app/tools/work_order_client.py`
- Create: `infra/otel/otel-collector-config.yaml`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/observability/TelemetryTest.java`
- Test: `apps/ai-service/tests/test_observability.py`

**Interfaces:**
- Trace path: HTTP -> AI route -> retrieval/model -> proposal -> Java command -> DB/Outbox -> connector -> upstream -> reconciliation.
- Allowed custom attributes: request ID, tenant UUID, proposal/job ID, connector type, generic upstream status and stable error code.
- Forbidden: prompt/message, SQL/parameters, token, contact values, attachment URL, full body.

- [ ] **Step 1: Write RED in-memory exporter tests**

Create one cross-service synthetic request with fixed `traceparent`, collect spans, assert parent/child linkage and scan every name/attribute/event for injected canary secrets, prompt text, SQL values and contact values.

- [ ] **Step 2: Run RED**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=TelemetryTest test
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/test_observability.py -q
```

- [ ] **Step 3: Instrument with allowlisted wrappers**

Use W3C propagation and OTLP exporters configured by environment. Add custom spans only through helper functions that accept enumerated attribute keys; never attach arbitrary request dictionaries. Disable exporter cleanly when endpoint is unset.

- [ ] **Step 4: Run GREEN and commit**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=TelemetryTest test
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/test_observability.py -q
git add apps/work-order-service/pom.xml apps/work-order-service/src/main/java/com/tangmeng/workorder/observability apps/work-order-service/src/test/java/com/tangmeng/workorder/observability apps/ai-service/pyproject.toml apps/ai-service/app apps/ai-service/tests/test_observability.py infra/otel
git commit -m "feat(obs): trace AI and work order operations"
```

## Task 7: Publish bounded Prometheus metrics

**Files:**
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/observability/WorkOrderMetrics.java`
- Create: `apps/ai-service/app/metrics.py`
- Modify: `apps/ai-service/app/main.py`
- Create: `infra/prometheus/prometheus.yml`
- Modify: `docker-compose.yml`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/observability/MetricsTest.java`
- Test: `apps/ai-service/tests/test_metrics.py`

**Interfaces:**
- Metrics cover proposal lifecycle, command latency/conflict/idempotency, connector outcome/reconciliation/circuit, quality jobs/model usage, NL2SQL generation/rejection/cost/timeout/rows, retrieval modes/degradation.
- Labels use bounded enums only; tenant/user/order/question/prompt IDs are forbidden labels.

- [ ] **Step 1: Write RED metric name/label tests**

Exercise each major outcome, scrape `/actuator/prometheus` and Python `/metrics`, assert approved counters/histograms exist and no canary tenant/user/work-order value appears as a label.

- [ ] **Step 2: Run RED, implement Micrometer/prometheus-client wrappers, run GREEN**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=MetricsTest test
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/test_metrics.py -q
```

Use fixed label keys such as `outcome`, `action`, `mode`, `provider`, `error_code`; normalize unknown error codes to `other`.

- [ ] **Step 3: Commit**

```powershell
git add apps/work-order-service/src/main/java/com/tangmeng/workorder/observability apps/work-order-service/src/test/java/com/tangmeng/workorder/observability apps/ai-service/app apps/ai-service/tests/test_metrics.py infra/prometheus docker-compose.yml
git commit -m "feat(obs): expose bounded operational metrics"
```

## Task 8: Enforce a synthetic privacy gate in CI

**Files:**
- Create: `scripts/privacy_scan.ps1`
- Create: `tests/privacy/fixtures/rejected/private-path.txt.fixture`
- Create: `tests/privacy/fixtures/rejected/secret.txt.fixture`
- Create: `tests/privacy/fixtures/allowed/synthetic-config.txt.fixture`
- Modify: `.github/workflows/ci.yml`
- Modify: `.dockerignore`
- Test: `tests/privacy/privacy_scan.Tests.ps1`

**Interfaces:**
- Scanner checks tracked source/config/docs, `git diff --cached`, and Docker build-context manifests.
- Rejected categories: absolute private paths, internal package/host/repo/API patterns, real identifiers, keys/tokens/certificates/passwords/image URLs and suspicious copied blocks.
- Fixture values are synthetic canaries, not real private identifiers.

- [ ] **Step 1: Write RED Pester/PowerShell self-tests**

Assert each rejected fixture causes a nonzero exit with a category and relative file, allowed fixture passes, binary/vendor/build outputs are skipped deliberately, and staged-only violations are detected. Assert the report redacts the matched secret value.

- [ ] **Step 2: Run RED**

```powershell
Invoke-Pester tests/privacy/privacy_scan.Tests.ps1 -CI
```

- [ ] **Step 3: Implement fail-closed scanning**

Enumerate files via `git ls-files` and staged names via `git diff --cached --name-only --diff-filter=ACMR`; inspect Docker contexts from Dockerfiles/.dockerignore; use category-specific synthetic regexes and entropy checks. Do not encode actual company/customer/private-repo names in the scanner or fixtures.

- [ ] **Step 4: Run GREEN and commit**

```powershell
Invoke-Pester tests/privacy/privacy_scan.Tests.ps1 -CI
powershell -NoProfile -File scripts/privacy_scan.ps1
git add scripts/privacy_scan.ps1 tests/privacy .github/workflows/ci.yml .dockerignore
git commit -m "ci: block private data from public artifacts"
```

Expected: self-tests prove violations fail while the repository itself passes.

## Task 9: Verify both connector modes and production-readiness evidence

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Create: `docs/connector-configuration.md`
- Create: `docs/operations.md`
- Modify: `scripts/smoke_test.py`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add Local and HTTP smoke scenarios**

Local scenario reruns all prior phases. HTTP scenario starts a generic Mock upstream, confirms one proposal, simulates success and applied-then-timeout recovery, verifies no duplicate upstream create, accepts one signed Webhook, advances one polling cursor, and observes one connected trace plus metrics.

- [ ] **Step 2: Run the complete repository verification**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml test
.\.venv\Scripts\python.exe -m ruff check apps/ai-service
.\.venv\Scripts\python.exe -m mypy apps/ai-service/app
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests -q
Invoke-Pester tests/privacy/privacy_scan.Tests.ps1 -CI
powershell -NoProfile -File scripts/privacy_scan.ps1
docker compose --profile local up --build -d
python scripts/smoke_test.py --scenario all
docker compose --profile local down
docker compose --profile http up --build -d
python scripts/smoke_test.py --scenario connector-http
docker compose --profile http down
git diff --check
```

Expected: both connector modes pass the same contract; applied-then-timeout produces one upstream write; trace/metrics contain no sensitive canary; privacy scan passes; `git diff --check` is silent.

- [ ] **Step 3: Verify the external-reference boundary and commit only public files**

```powershell
git status --short
git add .env.example README.md docs scripts/smoke_test.py docker-compose.yml
git diff --cached --name-only
git commit -m "docs: complete production connector operations guide"
git status --short --branch
```

Expected: no external-reference identifier or path appears in the public diff; staged paths are only under the public project; the public worktree is clean and no push is performed. The operator separately confirms any external read-only reference remains unchanged without recording its identity in this repository.
