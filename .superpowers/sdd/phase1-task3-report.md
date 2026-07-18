# Phase 1 / Task 3 implementation report

## Status

Implemented the authenticated tenant security boundary for the Java work-order service. A signature-validated JWT supplies the provisional tenant ID used for the current-authority lookup; the resulting `TenantContext` contains only the intersection of JWT roles/projects and ACTIVE database membership/project-scope rows.

## RED evidence

Tests were created before production classes:

- `TenantContextResolverTest`: claim validation, current-user fail-closed behavior, JWT/DB role and project intersection, issuer and audience validation.
- `WorkOrderAuthorizationTest`: stable 401 without a token, stable 403 for an authenticated identity with no current tenant role, and stable 404 for an inaccessible order.
- `TenantTransactionTest`: transaction begins, transaction-local tenant is set, then business code runs and commits.
- `TenantAccessServiceTest`: current identity, membership and project scope are loaded through the provisional tenant transaction.

Command (PowerShell requires quoting the comma-containing Maven property):

```powershell
mvn -f apps/work-order-service/pom.xml '-Dtest=TenantContextResolverTest,WorkOrderAuthorizationTest' test
```

Observed at 2026-07-18 15:07:30 +08:00: `BUILD FAILURE` during `testCompile`, with eight expected missing-symbol errors for `SecurityConfig`, `TenantContext`, `TenantContextResolver`, and `TenantAccessService`. This was the expected RED because the security/context production classes did not exist.

An earlier unquoted attempt was rejected by PowerShell's parser at the comma and did not count as RED evidence.

## GREEN evidence

Focused command after implementation and refactor:

```powershell
mvn -f apps/work-order-service/pom.xml '-Dtest=TenantContextResolverTest,WorkOrderAuthorizationTest,TenantTransactionTest,TenantAccessServiceTest' test
```

Observed at 2026-07-18 15:12:19 +08:00: `BUILD SUCCESS`; 11 tests run, 0 failures, 0 errors, 0 skipped.

## Full-suite evidence

```powershell
mvn -f apps/work-order-service/pom.xml test
```

Observed at 2026-07-18 15:12:33 +08:00: `BUILD SUCCESS`; 31 tests run, 0 failures, 0 errors, 7 skipped. The seven PostgreSQL/Testcontainers tests were skipped because Docker is unavailable, as expected for this environment.

## Implementation notes

- `SecurityConfig` is stateless, enables OAuth2 resource-server JWT authentication, permits only `/actuator/health` and its probe subpaths, requires a current tenant role for `/api/**` and `/internal/**`, and denies every other route.
- The decoder uses the configured issuer and a mandatory configured audience validator.
- `TenantContextResolver` rejects missing, blank, wrongly typed, and malformed tenant/subject/role/project claims before database access.
- `TenantAccessService` filters the configured issuer plus subject and selects only ACTIVE user, tenant, membership, project-scope, and project rows.
- The database values are intersected with the token values; neither database-only nor token-only authorities enter `TenantContext`.
- `TenantTransaction.required` uses a `TransactionTemplate` and executes `select set_config('app.tenant_id', ?, true)` before the supplied mapper/business access. The `true` flag keeps the setting transaction-local.
- The temporary prior-task `SecurityAutoConfiguration` exclusion was removed from `application.yml` now that an explicit filter chain exists. Existing MVC behavior tests were updated with an authenticated test principal.
- Existing query controllers and mapper/query signatures were deliberately not refactored; that remains Task 5. The 404 contract is preserved through the existing not-found boundary, without disclosing cross-tenant resource existence.

## Security self-review

- Claim parsing: fail-closed for null JWT, blank subject, malformed tenant UUID, scalar/non-collection authority claims, blank roles, and malformed project UUIDs.
- Issuer/audience: both enforced by the configured decoder validator and unit-tested with valid, wrong-audience, and wrong-issuer tokens.
- Current authority: configured issuer/subject and ACTIVE database state are required; roles/projects use DB-intersection semantics.
- RLS locality: only the already decoded JWT tenant claim is used provisionally, and it is applied through transaction-local `set_config` before all current-authority queries.
- Error stability: security handlers emit `ApiError` bodies with `UNAUTHORIZED`/`FORBIDDEN`; inaccessible resources retain `WORK_ORDER_NOT_FOUND` and HTTP 404.
- No request-body tenant value is accepted or consulted.

## Remaining limitations

- PostgreSQL execution of the new current-authority queries could not be exercised locally because Docker is unavailable; query and transaction behavior are covered with focused unit tests, while prior schema/RLS integration tests remain skipped.
- Project-specific order filtering is intentionally deferred to Task 5. This task establishes the authenticated/intersected context and coarse boundary without prematurely changing query mapper signatures.
