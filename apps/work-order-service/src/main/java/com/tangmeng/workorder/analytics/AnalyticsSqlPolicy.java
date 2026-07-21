package com.tangmeng.workorder.analytics;

import net.sf.jsqlparser.JSQLParserException;
import net.sf.jsqlparser.parser.CCJSqlParserUtil;
import net.sf.jsqlparser.statement.Statement;
import net.sf.jsqlparser.statement.select.PlainSelect;
import net.sf.jsqlparser.statement.select.Select;
import net.sf.jsqlparser.expression.Expression;
import net.sf.jsqlparser.schema.Table;
import org.springframework.stereotype.Component;

import java.util.Set;
import java.util.regex.Pattern;

/**
 * Java-side SQL policy validator using JSQLParser.
 * Independently re-validates SQL that passed Python-side checks.
 */
@Component
public class AnalyticsSqlPolicy {

    private static final Set<String> ALLOWED_VIEWS = Set.of(
        "analytics_work_order_v",
        "analytics_quality_v",
        "analytics_rectification_v"
    );

    private static final Set<String> BLOCKED_SCHEMAS = Set.of(
        "pg_catalog", "information_schema", "pg_toast"
    );

    private static final Set<String> BLOCKED_FUNCTIONS = Set.of(
        "pg_sleep", "pg_terminate_backend", "pg_cancel_backend",
        "lo_import", "lo_export", "dblink", "copy"
    );

    private static final Pattern INJECTION_PATTERN = Pattern.compile(
        "(;\\s*\\w|--|/\\*.*\\*/|UNION\\s+ALL\\s+SELECT|INTO\\s+(OUTFILE|DUMPFILE))",
        Pattern.CASE_INSENSITIVE | Pattern.DOTALL
    );

    /**
     * Validate SQL against the policy.
     *
     * @param sql the SQL to validate
     * @throws SqlPolicyViolation if the SQL violates policy
     */
    public void validate(String sql) throws SqlPolicyViolation {
        if (sql == null || sql.isBlank()) {
            throw new SqlPolicyViolation("SQL is empty", "JAVA_PARSE");
        }

        // Pre-parse injection check
        if (INJECTION_PATTERN.matcher(sql).find()) {
            throw new SqlPolicyViolation("SQL contains blocked pattern", "JAVA_POLICY");
        }

        // Parse
        Statement stmt;
        try {
            stmt = CCJSqlParserUtil.parse(sql);
        } catch (JSQLParserException e) {
            throw new SqlPolicyViolation("SQL parse error: " + e.getMessage(), "JAVA_PARSE");
        }

        // Must be a SELECT
        if (!(stmt instanceof Select select)) {
            throw new SqlPolicyViolation(
                "Only SELECT allowed, got " + stmt.getClass().getSimpleName(),
                "JAVA_POLICY"
            );
        }

        PlainSelect plainSelect = select.getPlainSelect();
        if (plainSelect == null) {
            throw new SqlPolicyViolation("Only simple SELECT allowed", "JAVA_POLICY");
        }

        // Check FROM table
        if (plainSelect.getFromItem() instanceof Table table) {
            validateTable(table);
        }

        // Check JOINs
        if (plainSelect.getJoins() != null) {
            for (var join : plainSelect.getJoins()) {
                if (join.getRightItem() instanceof Table table) {
                    validateTable(table);
                }
            }
        }

        // Check for FOR UPDATE
        if (plainSelect.getForUpdateTable() != null) {
            throw new SqlPolicyViolation("FOR UPDATE is not allowed", "JAVA_POLICY");
        }
    }

    private void validateTable(Table table) throws SqlPolicyViolation {
        String schema = table.getSchemaName();
        if (schema != null && BLOCKED_SCHEMAS.contains(schema.toLowerCase())) {
            throw new SqlPolicyViolation(
                "Schema '" + schema + "' is not allowed",
                "JAVA_POLICY"
            );
        }

        String name = table.getName();
        if (!ALLOWED_VIEWS.contains(name.toLowerCase())) {
            throw new SqlPolicyViolation(
                "View '" + name + "' is not in the allowed list",
                "JAVA_POLICY"
            );
        }
    }

    /**
     * Exception for SQL policy violations.
     */
    public static class SqlPolicyViolation extends Exception {
        private final String stage;

        public SqlPolicyViolation(String message, String stage) {
            super(message);
            this.stage = stage;
        }

        public String getStage() {
            return stage;
        }
    }
}
