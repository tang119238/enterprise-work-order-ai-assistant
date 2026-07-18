# Phase 1 Task 7 implementation report

## Outcome

Implemented human-confirmed and human-rejected action proposals with optimistic locking, audit events,
assignment intervals, outbox writes, canonical idempotency, current-authority rechecks, and transaction
recovery on branch `codex/production-evolution`. No push was performed and the main checkout was not
modified.

Implementation commit: `15fc068b56b23433a01b123ec8dbc23ce3217436`

Commit subject: `feat(java): execute confirmed work order commands`

## API assumption requiring reviewer scrutiny

The written specification requires a strict confirmation body but does not name its fields. Task 7 uses
one strict shared body type with an exact decision literal:

- confirm endpoint: `{"decision":"CONFIRM"}`
- reject endpoint: `{"decision":"REJECT"}`

Endpoint and decision must match. Missing, differently cased, opposite, unknown, or authority fields are
rejected as HTTP 422 `INVALID_COMMAND`. This assumption should be confirmed before Task 8 publishes API
examples.

## Contract implemented

- `POST /api/action-proposals/{id}/confirm` requires a nonblank `Idempotency-Key` and the strict confirm body.
- `POST /api/action-proposals/{id}/reject` atomically records the current authorized human decision and never
  mutates a work order, event, or outbox row.
- `AI_SERVICE` is denied confirmation and rejection even if another role is also present.
- Confirmation reloads the proposal, current database identity, exact effective role intersection, current
  project-scope intersection, target/project, current assignee, state, and version inside the tenant transaction.
- The successful order is idempotency replay lookup, atomic proposal claim to `EXECUTING`, tenant/project/version
  guarded work-order write, assignment interval update when assignee changes, immutable event, outbox event,
  canonical idempotent response, and proposal `EXECUTED`.
- `CREATE`, `ASSIGN`, `UPDATE`, `ACCEPT`, `START`, `COMPLETE`, `CLOSE`, and `CANCEL` are executed. Existing work
  orders increment exactly one version; CREATE starts at version 0.
- The confirmation hash is SHA-256 over the operation's canonical request body containing exact decision and
  proposal ID. JSON object order is deterministic. A key replay with the same hash returns the stored response;
  the same tenant/operation/key with a different hash returns 409 `IDEMPOTENCY_KEY_CONFLICT` without execution.
- Proposal claim is one SQL conditional update over eligible, unexpired `PENDING_CONFIRMATION` or same-human
  `CONFIRMED` state. The command service then uses only the freshly loaded proposal and database facts.
- Any command failure rolls back the primary tenant transaction. A later `TenantTransaction.required` recovery
  call records `FAILED/error_code`; expiry recovery records `EXPIRED/ACTION_PROPOSAL_EXPIRED`. Recovery failure is
  deliberately unable to mask the original stable exception.
- Stable mappings are present for 403 `ACTION_NOT_PERMITTED`, 404 `WORK_ORDER_NOT_FOUND`, 409
  `WORK_ORDER_VERSION_CONFLICT` with a freshly recomputed preview, 409 `INVALID_STATE_TRANSITION`, 410
  `ACTION_PROPOSAL_EXPIRED`, and 422 `INVALID_COMMAND`.
- Event JSON snapshots use PostgreSQL JSONB type handling for insert and generated reads. UUID handling is
  registered through the application MyBatis configuration. Idempotency and outbox JSON are explicitly cast to
  JSONB on insert and parsed through Jackson on read.

## Files

Production additions:

- `api/ApiErrorWithPreview.java`
- `api/ConfirmProposalRequest.java`
- `api/WorkOrderExecutionResponse.java`
- `command/ActionProposalExpiredException.java`
- `command/IdempotencyConflictException.java`
- `command/JdbcWorkOrderCommandRepository.java`
- `command/WorkOrderCommandRepository.java`
- `command/WorkOrderCommandService.java`
- `command/WorkOrderVersionConflictException.java`
- `domain/WorkOrderEventEntity.java`
- `mapper/WorkOrderEventMapper.java`

Production modifications:

- `command/ActionProposalService.java`
- `config/MybatisPlusConfig.java`
- `controller/ActionProposalController.java`
- `controller/GlobalExceptionHandler.java`

Tests:

- `api/ConfirmProposalRequestTest.java`
- `command/WorkOrderCommandIntegrationTest.java`
- `controller/ActionProposalControllerTest.java`
- `integration/IdempotencyConcurrencyTest.java`
- `mapper/WorkOrderEventMapperMetadataTest.java`

## TDD evidence

### Initial RED

Command:

```powershell
mvn -f apps/work-order-service/pom.xml '-Dtest=ConfirmProposalRequestTest,ActionProposalControllerTest,WorkOrderCommandIntegrationTest' test
```

Observed expected failure before production implementation: test compilation failed on the absent
`WorkOrderCommandService`, `WorkOrderCommandRepository`, `WorkOrderExecutionResponse`, and
`WorkOrderEventEntity` types. This was the missing feature boundary rather than a test setup error.

### Header RED

Command:

```powershell
mvn -f apps/work-order-service/pom.xml '-Dtest=ActionProposalControllerTest#confirmsOnlyWithMatchingStrictBodyAndNonblankIdempotencyKey' test
```

Observed expected assertion failure after adding the missing-header case: expected 422, actual 500 from
`MissingRequestHeaderException`. The controller now binds the header as optional and performs stable command
validation itself.

### Mapper RED

Command:

```powershell
mvn -f apps/work-order-service/pom.xml clean test
```

Observed one real error in 136 discovered tests: `WorkOrderEventMapperMetadataTest` failed with
`No typehandler found for property id` when automatic JSON result mapping initialized. The minimal production
fix registered `UuidTypeHandler` through `MybatisPlusConfig`; the same test then passed.

### Final focused GREEN

Command:

```powershell
mvn -f apps/work-order-service/pom.xml '-Dtest=WorkOrderCommandIntegrationTest,ActionProposalControllerTest,ConfirmProposalRequestTest,WorkOrderEventMapperMetadataTest,IdempotencyConcurrencyTest' test
```

Result:

- Tests discovered: 30
- Executable tests passed: 28
- Failures: 0
- Errors: 0
- Skipped: 2
- Maven exit code: 0

The two skips are the Task 7 PostgreSQL/Testcontainers tests described below; they are not reported as passed.

Non-Docker coverage includes all eight actions, exact versioning, assignment interval calls, event/outbox/idempotency
order, strict controller body/header behavior, same-key replay/conflict, deterministic hash behavior, expiry, stale
preview, current permission revocation, cross-tenant/project hiding, self-assignment, AI denial, invalid state,
atomic rejection, and forced event failure stopping every later primary-transaction write.

## Fresh full Java suite

Command:

```powershell
mvn -f apps/work-order-service/pom.xml clean test
```

Result:

- Tests discovered: 140
- Executable tests passed: 127
- Failures: 0
- Errors: 0
- Skipped: 13
- Maven exit code: 0

Exact Docker/Testcontainers skips:

- `ActionProposalMapperIntegrationTest`: 1 skipped
- `IdempotencyConcurrencyTest`: 2 skipped
- `TenantRlsIntegrationTest`: 3 skipped
- `TenantSchemaIntegrationTest`: 5 skipped
- `WorkOrderPostgresIntegrationTest`: 2 skipped

Docker reported `Could not find a valid Docker environment`. The two Task 7 skipped tests compile and are honestly
gated but were not executed:

1. two simultaneous same-key confirmations yield equal responses and exactly one version increment, event, outbox,
   and idempotency row;
2. a real PostgreSQL trigger forces event insertion failure and verifies work-order/outbox/idempotency rollback plus
   separate `FAILED` recovery.

## Repository verification

- `git diff --check`: exit 0 before the implementation commit.
- `git diff --cached --check`: exit 0 before the implementation commit.
- Implementation commit: `15fc068b56b23433a01b123ec8dbc23ce3217436`.
- No restricted reference path or identifier occurs in the changed public artifacts.
- No push performed.
