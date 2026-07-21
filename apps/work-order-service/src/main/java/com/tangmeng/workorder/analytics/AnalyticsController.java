package com.tangmeng.workorder.analytics;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.*;

/**
 * Internal analytics execution endpoint.
 * Called by Python AI service with service token and user context.
 */
@RestController
@RequestMapping("/internal/analytics")
public class AnalyticsController {

    private final AnalyticsSqlPolicy sqlPolicy;
    private final AnalyticsCostGuard costGuard;
    private final AnalyticsExecutor executor;
    private final AnalyticsAuditService auditService;

    public AnalyticsController(
        AnalyticsSqlPolicy sqlPolicy,
        AnalyticsCostGuard costGuard,
        AnalyticsExecutor executor,
        AnalyticsAuditService auditService
    ) {
        this.sqlPolicy = sqlPolicy;
        this.costGuard = costGuard;
        this.executor = executor;
        this.auditService = auditService;
    }

    @PostMapping("/execute")
    public ResponseEntity<Map<String, Object>> execute(
        @RequestBody Map<String, Object> request,
        HttpServletRequest httpRequest
    ) {
        String sql = (String) request.get("sql");
        String catalogVersion = (String) request.get("catalog_version");

        // Extract user context from headers
        String tenantId = httpRequest.getHeader("X-Tenant-Id");
        String userId = httpRequest.getHeader("X-User-Id");
        String projectIdsStr = httpRequest.getHeader("X-Project-Ids");
        String requestId = httpRequest.getHeader("X-Request-Id");
        String traceId = httpRequest.getHeader("X-Trace-Id");

        List<String> projectIds = projectIdsStr != null
            ? Arrays.asList(projectIdsStr.split(","))
            : Collections.emptyList();

        // Validate tenant context
        if (tenantId == null || tenantId.isBlank()) {
            return errorResponse(HttpStatus.FORBIDDEN, "ANALYTICS_NOT_PERMITTED",
                "Missing tenant context", null);
        }

        // Step 1: Java-side SQL validation
        try {
            sqlPolicy.validate(sql);
        } catch (AnalyticsSqlPolicy.SqlPolicyViolation e) {
            String auditId = auditService.recordAudit(
                tenantId, userId, sql, catalogVersion,
                null, null, sql, e.getStage(), e.getMessage(),
                false, null, null, false, requestId, traceId
            );
            return errorResponse(HttpStatus.UNPROCESSABLE_ENTITY, "SQL_POLICY_VIOLATION",
                e.getMessage(), auditId);
        }

        // Step 2: Cost check
        try {
            costGuard.checkCost(sql);
        } catch (AnalyticsCostGuard.CostLimitExceeded e) {
            String auditId = auditService.recordAudit(
                tenantId, userId, sql, catalogVersion,
                null, null, sql, "COST_CHECK", e.getMessage(),
                false, null, null, false, requestId, traceId
            );
            return errorResponse(HttpStatus.UNPROCESSABLE_ENTITY, "SQL_COST_LIMIT_EXCEEDED",
                e.getMessage(), auditId);
        }

        // Step 3: Execute
        try {
            AnalyticsExecutor.ExecuteResult result = executor.execute(sql, tenantId, projectIds);

            String auditId = auditService.recordAudit(
                tenantId, userId, sql, catalogVersion,
                null, null, sql, "EXECUTED", null,
                true, (int) result.executionMs(), result.rowCount(),
                result.truncated(), requestId, traceId
            );

            Map<String, Object> response = new LinkedHashMap<>();
            response.put("columns", result.columns());
            response.put("rows", result.rows());
            response.put("truncated", result.truncated());
            response.put("execution_ms", result.executionMs());
            response.put("row_count", result.rowCount());
            response.put("audit_id", auditId);

            return ResponseEntity.ok(response);

        } catch (java.sql.SQLException e) {
            String errorCode = "SQL_EXECUTION_TIMEOUT";
            int status = HttpStatus.GATEWAY_TIMEOUT.value();

            if (!e.getMessage().contains("timeout")) {
                errorCode = "ANALYTICS_UNAVAILABLE";
                status = HttpStatus.SERVICE_UNAVAILABLE.value();
            }

            String auditId = auditService.recordAudit(
                tenantId, userId, sql, catalogVersion,
                null, null, sql, "REJECTED", e.getMessage(),
                false, null, null, false, requestId, traceId
            );

            return errorResponse(HttpStatus.valueOf(status), errorCode,
                e.getMessage(), auditId);
        }
    }

    private ResponseEntity<Map<String, Object>> errorResponse(
        HttpStatus status, String errorCode, String message, String auditId
    ) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("error_code", errorCode);
        body.put("message", message);
        if (auditId != null) {
            body.put("audit_id", auditId);
        }
        return ResponseEntity.status(status).body(body);
    }
}
