package com.tangmeng.workorder.analytics;

import org.springframework.stereotype.Component;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;

/**
 * Checks query cost via EXPLAIN before execution.
 * Rejects queries with estimated cost > 100000 or rows > 1000000.
 */
@Component
public class AnalyticsCostGuard {

    private static final double MAX_COST = 100_000.0;
    private static final double MAX_ROWS = 1_000_000.0;

    private final DataSource analyticsDataSource;

    public AnalyticsCostGuard(DataSource analyticsDataSource) {
        this.analyticsDataSource = analyticsDataSource;
    }

    /**
     * Validate query cost using EXPLAIN.
     *
     * @param sql the SQL to check
     * @throws CostLimitExceeded if cost or row estimate exceeds limits
     */
    public void checkCost(String sql) throws CostLimitExceeded {
        String explainSql = "EXPLAIN (FORMAT JSON) " + sql;

        try (Connection conn = analyticsDataSource.getConnection();
             PreparedStatement ps = conn.prepareStatement(explainSql);
             ResultSet rs = ps.executeQuery()) {

            if (rs.next()) {
                String planJson = rs.getString(1);
                parseAndCheckCost(planJson);
            }
        } catch (SQLException e) {
            throw new CostLimitExceeded("EXPLAIN failed: " + e.getMessage());
        }
    }

    private void parseAndCheckCost(String planJson) throws CostLimitExceeded {
        // Simple extraction from JSON plan
        // Format: [{"Plan": {"Total Cost": ..., "Plan Rows": ...}}]
        double totalCost = extractDouble(planJson, "\"Total Cost\"");
        double planRows = extractDouble(planJson, "\"Plan Rows\"");

        if (totalCost > MAX_COST) {
            throw new CostLimitExceeded(
                String.format("Estimated cost %.0f exceeds limit %.0f", totalCost, MAX_COST)
            );
        }
        if (planRows > MAX_ROWS) {
            throw new CostLimitExceeded(
                String.format("Estimated rows %.0f exceeds limit %.0f", planRows, MAX_ROWS)
            );
        }
    }

    private double extractDouble(String json, String key) {
        int idx = json.indexOf(key);
        if (idx < 0) return 0;
        int colonIdx = json.indexOf(':', idx + key.length());
        if (colonIdx < 0) return 0;
        int start = colonIdx + 1;
        while (start < json.length() && json.charAt(start) == ' ') start++;
        int end = start;
        while (end < json.length() && (Character.isDigit(json.charAt(end)) || json.charAt(end) == '.')) end++;
        try {
            return Double.parseDouble(json.substring(start, end));
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    /**
     * Exception when query cost exceeds limits.
     */
    public static class CostLimitExceeded extends Exception {
        public CostLimitExceeded(String message) {
            super(message);
        }
    }
}
