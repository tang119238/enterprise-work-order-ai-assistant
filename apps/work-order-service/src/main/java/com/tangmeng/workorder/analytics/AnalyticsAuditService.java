package com.tangmeng.workorder.analytics;

import org.springframework.stereotype.Service;

import javax.sql.DataSource;
import java.sql.*;
import java.util.UUID;

/**
 * Records analytics query audit trail.
 */
@Service
public class AnalyticsAuditService {

    private final DataSource analyticsDataSource;

    public AnalyticsAuditService(DataSource analyticsDataSource) {
        this.analyticsDataSource = analyticsDataSource;
    }

    /**
     * Record an analytics query audit entry.
     *
     * @return audit ID
     */
    public String recordAudit(
        String tenantId,
        String userId,
        String questionSummary,
        String catalogVersion,
        String modelProvider,
        String modelName,
        String generatedSql,
        String validationStage,
        String rejectionReason,
        boolean executed,
        Integer executionMs,
        Integer rowCount,
        boolean truncated,
        String requestId,
        String traceId
    ) {
        String auditId = UUID.randomUUID().toString();

        String sql = """
            INSERT INTO analytics_query_audit (
                id, tenant_id, user_id, question_summary, catalog_version,
                model_provider, model_name, generated_sql, validation_stage,
                rejection_reason, executed, execution_ms, row_count,
                truncated, request_id, trace_id
            ) VALUES (?, ?::uuid, ?::uuid, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """;

        try (Connection conn = analyticsDataSource.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {

            ps.setObject(1, UUID.fromString(auditId));
            ps.setString(2, tenantId);
            ps.setString(3, userId);
            ps.setString(4, truncate(questionSummary, 500));
            ps.setString(5, catalogVersion);
            ps.setString(6, modelProvider);
            ps.setString(7, modelName);
            ps.setString(8, generatedSql);
            ps.setString(9, validationStage);
            ps.setString(10, rejectionReason);
            ps.setBoolean(11, executed);
            ps.setObject(12, executionMs);
            ps.setObject(13, rowCount);
            ps.setBoolean(14, truncated);
            ps.setString(15, requestId);
            ps.setString(16, traceId);

            ps.executeUpdate();
        } catch (SQLException e) {
            // Log but don't fail the request
            System.err.println("Failed to record analytics audit: " + e.getMessage());
        }

        return auditId;
    }

    private String truncate(String value, int maxLen) {
        if (value == null) return null;
        return value.length() > maxLen ? value.substring(0, maxLen) : value;
    }
}
