package com.tangmeng.workorder.analytics;

import org.springframework.stereotype.Component;

import javax.sql.DataSource;
import java.sql.*;
import java.util.*;

/**
 * Executes validated read-only analytics queries.
 * Sets transaction to READ ONLY, applies statement timeout,
 * and enforces row/column/byte limits.
 */
@Component
public class AnalyticsExecutor {

    private static final int MAX_ROWS = 200;
    private static final int MAX_COLUMNS = 50;
    private static final int MAX_RESULT_BYTES = 1_048_576; // 1 MB

    private final DataSource analyticsDataSource;

    public AnalyticsExecutor(DataSource analyticsDataSource) {
        this.analyticsDataSource = analyticsDataSource;
    }

    /**
     * Execute a validated SQL query.
     *
     * @param sql the validated SQL
     * @param tenantId the tenant context
     * @param projectIds the project scope
     * @return execution result
     * @throws SQLException on database errors
     */
    public ExecuteResult execute(String sql, String tenantId, List<String> projectIds) throws SQLException {
        long start = System.currentTimeMillis();

        try (Connection conn = analyticsDataSource.getConnection()) {
            conn.setAutoCommit(false);

            // Set transaction to read only
            conn.setReadOnly(true);

            // Set statement timeout (3 seconds)
            try (Statement stmt = conn.createStatement()) {
                stmt.execute("SET LOCAL statement_timeout = '3000ms'");
                stmt.execute("SET LOCAL lock_timeout = '500ms'");
            }

            // Set tenant and project context for RLS
            try (Statement stmt = conn.createStatement()) {
                stmt.execute("SET LOCAL app.tenant_id = '" + tenantId + "'");
                String projectArray = "{"
                    + String.join(",", projectIds)
                    + "}";
                stmt.execute("SET LOCAL app.project_ids = '" + projectArray + "'");
            }

            // Execute query
            List<String> columns = new ArrayList<>();
            List<List<Object>> rows = new ArrayList<>();
            boolean truncated = false;

            try (PreparedStatement ps = conn.prepareStatement(sql);
                 ResultSet rs = ps.executeQuery()) {

                ResultSetMetaData meta = rs.getMetaData();
                int colCount = Math.min(meta.getColumnCount(), MAX_COLUMNS);

                for (int i = 1; i <= colCount; i++) {
                    columns.add(meta.getColumnLabel(i));
                }

                int rowCount = 0;
                int totalBytes = 0;

                while (rs.next() && rowCount < MAX_ROWS) {
                    List<Object> row = new ArrayList<>();
                    for (int i = 1; i <= colCount; i++) {
                        Object value = rs.getObject(i);
                        String strValue = value != null ? value.toString() : "NULL";
                        totalBytes += strValue.length() * 2; // Approximate UTF-16

                        if (totalBytes > MAX_RESULT_BYTES) {
                            truncated = true;
                            break;
                        }

                        row.add(value);
                    }

                    if (truncated) break;

                    rows.add(row);
                    rowCount++;
                }

                if (!truncated && rs.next()) {
                    truncated = true;
                }
            }

            conn.commit();
            long executionMs = System.currentTimeMillis() - start;

            return new ExecuteResult(columns, rows, truncated, executionMs, rows.size());
        }
    }

    /**
     * Execution result.
     */
    public record ExecuteResult(
        List<String> columns,
        List<List<Object>> rows,
        boolean truncated,
        long executionMs,
        int rowCount
    ) {}
}
