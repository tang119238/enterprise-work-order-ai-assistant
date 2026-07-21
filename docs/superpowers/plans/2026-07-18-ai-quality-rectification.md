# AI 工单质检整改闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在工单完成后自动、幂等地运行规则与模型质检，保存不可变且可追溯的结果；失败或不确定时只创建整改建议，由质检人员确认后创建关联整改单，并支持多轮复检。

**Architecture:** Java 的完成事务通过 Outbox 发布事实事件，并拥有 `rectification_case`、操作建议和整改单。Python 通过受保护的内部事件 API 拉取完成事件，拥有质检任务、结果、发现和模型审计；CAS Worker 执行规则、混合检索和结构化模型判断，再幂等回调 Java。任务重试和回调补偿相互独立。

**Tech Stack:** Java 17/Spring Boot, PostgreSQL/Flyway/RLS, Python 3.12/FastAPI, SQLAlchemy async/Alembic, Pydantic JSON Schema, existing LLM gateway and hybrid retrieval, pytest/JUnit/Testcontainers.

## Global Constraints

- 依赖阶段 1 的确认式命令和阶段 2 的租户混合检索。
- AI 结果不是工单事实；Python 不直接写 `work_order`、`rectification_case` 或 `action_proposal`。
- `PASS/FAIL/UNCERTAIN/SKIP` 结果只追加不更新；人工覆盖另记事件。
- 无附件产生 `SKIP` 且不调用模型；确定性失败不得被模型改成通过。
- 任何模型 finding 的 `policy_chunk_id` 必须来自本次租户检索命中。

---

## File Structure Map

```text
apps/ai-service/alembic/versions/20260718_02_quality_loop.py
apps/ai-service/app/quality/
  models.py repository.py rules.py schema.py aggregator.py processor.py worker.py callback.py
apps/ai-service/tests/quality/
  test_repository.py test_rules.py test_schema.py test_aggregator.py
  test_processor.py test_worker.py test_callback.py test_quality_integration.py
apps/work-order-service/src/main/resources/db/migration/V6__quality_rectification.sql
apps/work-order-service/src/main/java/com/tangmeng/workorder/quality/
  QualityOutboxController.java QualityResultController.java RectificationService.java
  {QualityResultCallback,RectificationCaseEntity,ReviewEventEntity}.java
apps/work-order-service/src/test/java/com/tangmeng/workorder/quality/
  QualityOutboxControllerTest.java QualityResultIntegrationTest.java RectificationFlowIntegrationTest.java
```

## Task 1: Add Python quality tables and Java rectification ownership

**Files:**
- Create: `apps/ai-service/alembic/versions/20260718_02_quality_loop.py`
- Create: `apps/work-order-service/src/main/resources/db/migration/V6__quality_rectification.sql`
- Test: `apps/ai-service/tests/quality/test_quality_schema.py`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/quality/QualitySchemaIntegrationTest.java`

**Interfaces:**
- Python-owned: `quality_job`, `quality_result`, `quality_finding`, `model_call_audit`.
- Java-owned: `rectification_case`, `quality_review_event`.
- Business key `(tenant_id, work_order_id, work_order_version, inspection_round)` is unique; `quality_result.quality_job_id` is unique.
- Job status check allows exactly `PENDING`, `RUNNING`, `RETRY_WAIT`, `SUCCEEDED`, `FAILED`, `SKIPPED`; verdict check allows exactly `PASS`, `FAIL`, `UNCERTAIN`, `SKIP`.

- [ ] **Step 1: Write RED schema tests**

Assert all approved columns/status checks, immutable-result trigger or permission boundary, tenant-prefixed indexes, FORCE RLS, grants (`ai_app` can write only Python-owned tables), and duplicate business-key rejection.

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/quality/test_quality_schema.py -q
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=QualitySchemaIntegrationTest test
```

- [ ] **Step 3: Implement both migrations**

Use UUID primary keys, UTC `TIMESTAMPTZ`, JSONB snapshots, explicit status/verdict/severity checks, retry timestamps and foreign keys. `quality_result` and `quality_finding` have no update grant for `ai_app`; correction happens through append-only review events. Java tables refer to work orders by tenant + UUID, not by display number.

- [ ] **Step 4: Run GREEN and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/quality/test_quality_schema.py -q
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=QualitySchemaIntegrationTest test
git add apps/ai-service/alembic/versions/20260718_02_quality_loop.py apps/ai-service/tests/quality/test_quality_schema.py apps/work-order-service/src/main/resources/db/migration/V6__quality_rectification.sql apps/work-order-service/src/test/java/com/tangmeng/workorder/quality/QualitySchemaIntegrationTest.java
git commit -m "feat(db): add quality and rectification schema"
```

## Task 2: Publish and consume completed-work-order events idempotently

**Files:**
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/quality/QualityOutboxController.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/quality/QualityOutboxService.java`
- Modify: `apps/work-order-service/src/main/java/com/tangmeng/workorder/security/SecurityConfig.java`
- Create: `apps/ai-service/app/quality/event_client.py`
- Create: `apps/ai-service/app/quality/repository.py`
- Create: `apps/ai-service/app/quality/models.py`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/quality/QualityOutboxControllerTest.java`
- Test: `apps/ai-service/tests/quality/test_repository.py`

**Interfaces:**
- `POST /internal/quality-events/claim` accepts `limit<=50` and returns completion event ID, tenant, work-order snapshot/version, attachments summary and round.
- `POST /internal/quality-events/{eventId}/ack` acknowledges only after job creation succeeds.
- Only service scope `quality:consume` may use these endpoints.

- [ ] **Step 1: Write RED event and job-idempotency tests**

Test wrong scope 403, tenant context preservation, two consumers claiming no duplicate event, repeated event creates one job, ACK after successful DB commit, and network failure before ACK causing safe redelivery.

- [ ] **Step 2: Run RED**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=QualityOutboxControllerTest test
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/quality/test_repository.py -q
```

- [ ] **Step 3: Implement claim/create/ack**

Java claims Outbox with `FOR UPDATE SKIP LOCKED`, a five-minute lease and explicit attempt count. Python inserts `quality_job` using `ON CONFLICT (tenant_id, work_order_id, work_order_version, inspection_round) DO NOTHING`, reloads its ID, then ACKs. Event payload contains an immutable work-order snapshot, never a database credential or attachment URL.

- [ ] **Step 4: Run GREEN and commit**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=QualityOutboxControllerTest test
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/quality/test_repository.py -q
git add apps/work-order-service/src/main/java/com/tangmeng/workorder/quality apps/work-order-service/src/test/java/com/tangmeng/workorder/quality apps/ai-service/app/quality apps/ai-service/tests/quality/test_repository.py
git commit -m "feat(quality): create jobs from completion events"
```

## Task 3: Implement deterministic inspection rules

**Files:**
- Create: `apps/ai-service/app/quality/rules.py`
- Test: `apps/ai-service/tests/quality/test_rules.py`

**Interfaces:**
- `RuleEngine.evaluate(QualityInput) -> tuple[QualityFinding, ...]`.
- Stable rules: `REQUIRED_COMPLETION_SUMMARY`, `COMPLETED_AT_RANGE`, `SLA_COMPLETION`, `REQUIRED_ATTACHMENT`.
- Every finding has `source="RULE"`, stable severity/label, evidence and recommendation.

- [ ] **Step 1: Write RED table-driven rule tests**

Cover each pass/fail boundary, timezone-aware timestamps, completion before creation, due-time equality, missing/empty attachment list, and multiple simultaneous failures. Assert output ordering by the four rule codes above.

- [ ] **Step 2: Run RED, implement pure functions, run GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/quality/test_rules.py -q
```

No database, network, clock or LLM calls are permitted in `RuleEngine`; inject current time only when a rule needs it.

- [ ] **Step 3: Commit**

```powershell
git add apps/ai-service/app/quality/rules.py apps/ai-service/tests/quality/test_rules.py
git commit -m "feat(quality): add deterministic inspection rules"
```

## Task 4: Validate strict model output and aggregate verdicts

**Files:**
- Create: `apps/ai-service/app/quality/schema.py`
- Create: `apps/ai-service/app/quality/aggregator.py`
- Test: `apps/ai-service/tests/quality/test_schema.py`
- Test: `apps/ai-service/tests/quality/test_aggregator.py`

**Interfaces:**
- `QualityModelOutput` uses `extra="forbid"`, confidence `[0,1]`, exact verdict/severity enums and a bounded finding list.
- `validate_policy_evidence(output, retrieved_chunk_ids)` changes an ungrounded finding to `UNCERTAIN` and removes the invented chunk reference.
- Aggregate priority is deterministic rule FAIL > model FAIL > UNCERTAIN > SKIP > PASS.

- [ ] **Step 1: Write RED schema/aggregation tests**

Test success plus unknown fields, unknown rule, unknown enum, confidence below/above bounds, more than 20 findings, invalid chunk ID, deterministic FAIL overridden by model PASS, and empty model findings.

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/quality/test_schema.py apps/ai-service/tests/quality/test_aggregator.py -q
```

- [ ] **Step 3: Implement exact Pydantic contract and aggregation**

Allowed model rule codes are supplied from the versioned prompt policy catalog, never accepted from model output dynamically. Preserve the original truncated response in model audit when validation fails; do not persist hidden reasoning.

- [ ] **Step 4: Run GREEN and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/quality/test_schema.py apps/ai-service/tests/quality/test_aggregator.py -q
git add apps/ai-service/app/quality/schema.py apps/ai-service/app/quality/aggregator.py apps/ai-service/tests/quality/test_schema.py apps/ai-service/tests/quality/test_aggregator.py
git commit -m "feat(quality): validate and aggregate model findings"
```

## Task 5: Process a quality job and save immutable provenance

**Files:**
- Create: `apps/ai-service/app/quality/processor.py`
- Modify: `apps/ai-service/app/llm/contracts.py`
- Modify: `apps/ai-service/app/llm/gateway.py`
- Test: `apps/ai-service/tests/quality/test_processor.py`

**Interfaces:**
- `QualityProcessor.process(job: ClaimedQualityJob) -> QualityResultRecord`.
- Consumes tenant hybrid retrieval hits and `LLMGateway.generate_structured` with prompt version.
- Produces one result, findings and model-call audit in one transaction.

- [ ] **Step 1: Write RED processor matrix**

Test model success, timeout, 429, 503, authentication error, malformed JSON, schema error, ungrounded finding, no attachments, deterministic failure plus model pass, and duplicate processing after result already exists. Assert the model is not called for `SKIP`.

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/quality/test_processor.py -q
```

- [ ] **Step 3: Implement safe structured invocation**

Prompt includes only approved work-order snapshot fields, attachment summaries and retrieved policy chunks. Audit stores provider, model, prompt version, request ID, latency, token/cost if returned, request/response hashes and at most 8 KiB truncated response. It excludes full attachment URLs, contact fields and chain-of-thought. Save result/findings/audit atomically using the job ID uniqueness guard.

- [ ] **Step 4: Run GREEN and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/quality/test_processor.py -q
git add apps/ai-service/app/quality/processor.py apps/ai-service/app/llm apps/ai-service/tests/quality/test_processor.py
git commit -m "feat(quality): process auditable quality inspections"
```

## Task 6: Add CAS worker, retry classification and callback compensation

**Files:**
- Create: `apps/ai-service/app/quality/worker.py`
- Create: `apps/ai-service/app/quality/callback.py`
- Modify: `apps/ai-service/app/main.py`
- Test: `apps/ai-service/tests/quality/test_worker.py`
- Test: `apps/ai-service/tests/quality/test_callback.py`

**Interfaces:**
- CAS claim allows `PENDING` or due `RETRY_WAIT` -> `RUNNING` only once.
- Retry delay in minutes is `min(5 * 2 ** (retry_count - 1), 60)`; maximum model attempts is 3.
- Callback worker selects immutable results with `callback_at IS NULL` independent of job retry state.

- [ ] **Step 1: Write RED concurrency/retry tests**

Test two workers/one winner, delays 5/10/20, terminal failure after attempt 3, retryable timeout/429/5xx, nonretryable auth/schema/missing input, processor crash lease recovery, callback timeout redelivery and successful callback marking only after 2xx.

- [ ] **Step 2: Run RED, implement workers, run GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/quality/test_worker.py apps/ai-service/tests/quality/test_callback.py -q
```

Workers start through FastAPI lifespan with separate cancellation events and database sessions. They claim bounded batches and release resources on shutdown.

- [ ] **Step 3: Commit**

```powershell
git add apps/ai-service/app/quality apps/ai-service/app/main.py apps/ai-service/tests/quality
git commit -m "feat(quality): operate retry and callback workers"
```

## Task 7: Convert quality callbacks into human-confirmed Java proposals

**Files:**
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/quality/QualityResultCallback.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/quality/QualityResultController.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/quality/RectificationService.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/quality/RectificationCaseEntity.java`
- Modify: `apps/work-order-service/src/main/java/com/tangmeng/workorder/command/ActionProposalService.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/quality/QualityResultIntegrationTest.java`

**Interfaces:**
- `POST /internal/quality-results` requires `quality:callback`, result business key and `Idempotency-Key`.
- PASS creates `CLOSE` proposal; FAIL/UNCERTAIN creates `CREATE_RECTIFICATION` proposal; SKIP creates no state-changing proposal.
- Repeated callback reuses one rectification case/proposal.

- [ ] **Step 1: Write RED callback integration tests**

Assert untrusted user tokens cannot call internal endpoint, snapshot tenant/version is rechecked, PASS/FAIL/UNCERTAIN/SKIP mapping, duplicate delivery, no direct work-order mutation, and `AI_SERVICE` still cannot confirm resulting proposal.

- [ ] **Step 2: Run RED**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=QualityResultIntegrationTest test
```

- [ ] **Step 3: Implement idempotent callback mapping**

Persist current result ID on `rectification_case`, create a proposal through the existing authoritative preview service, and append a review event. Confirmation of `CREATE_RECTIFICATION` creates a `REWORK` order linked by root ID and advances case `PROPOSED -> RECTIFYING`; no other path may create it.

- [ ] **Step 4: Run GREEN and commit**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=QualityResultIntegrationTest test
git add apps/work-order-service/src/main/java/com/tangmeng/workorder apps/work-order-service/src/test/java/com/tangmeng/workorder/quality
git commit -m "feat(java): convert quality results to confirmed proposals"
```

## Task 8: Complete multi-round rectification and human overrides

**Files:**
- Modify: `apps/work-order-service/src/main/java/com/tangmeng/workorder/quality/RectificationService.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/api/QualityReviewRequest.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/controller/QualityReviewController.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/quality/RectificationFlowIntegrationTest.java`
- Test: `apps/ai-service/tests/quality/test_quality_integration.py`

**Interfaces:**
- Rectification completion emits next round with same case ID and incremented `inspection_round`.
- Human review appends `ACCEPTED`, `REJECTED` or `MODIFIED` plus mandatory reason; it never edits the AI result.
- Case closes only when latest round is PASS and its close proposal is confirmed by `QUALITY_REVIEWER`.

- [ ] **Step 1: Write RED end-to-end state tests**

Cover FAIL -> confirmed rework -> rework complete -> round 2 PASS -> confirmed close; round 2 FAIL remaining same case; stale round callback rejection; human modification audit; cross-tenant denial; and closing before latest PASS rejection.

- [ ] **Step 2: Run RED, implement transitions, run GREEN**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml -Dtest=RectificationFlowIntegrationTest test
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/quality/test_quality_integration.py -q
```

- [ ] **Step 3: Commit**

```powershell
git add apps/work-order-service/src/main/java apps/work-order-service/src/test/java/com/tangmeng/workorder/quality apps/ai-service/tests/quality/test_quality_integration.py
git commit -m "feat(quality): close the multi-round rectification loop"
```

## Task 9: Verify provenance and phase acceptance

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Create: `docs/api/quality-rectification.md`
- Modify: `scripts/smoke_test.py`

- [ ] **Step 1: Add a synthetic quality smoke scenario**

The smoke script completes a synthetic order, waits with a bounded timeout for one quality result, verifies evidence/model/prompt provenance, confirms one rectification proposal, completes the rework and verifies the next round. It must clean only data created under its unique synthetic tenant/run ID.

- [ ] **Step 2: Run full verification**

```powershell
apps\work-order-service\mvnw.cmd -f apps\work-order-service\pom.xml test
.\.venv\Scripts\python.exe -m ruff check apps/ai-service
.\.venv\Scripts\python.exe -m mypy apps/ai-service/app
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests -q
docker compose up --build -d
python scripts/smoke_test.py --scenario quality-loop
docker compose down
git diff --check
```

Expected: all suites pass; callback duplication creates one proposal; no unconfirmed rework exists; evidence, model and prompt provenance is traceable for 100% of non-SKIP results.

- [ ] **Step 3: Commit verified documentation**

```powershell
git add README.md docs scripts/smoke_test.py
git commit -m "docs: document auditable quality rectification"
git status --short --branch
```

Expected: clean worktree and no push.
