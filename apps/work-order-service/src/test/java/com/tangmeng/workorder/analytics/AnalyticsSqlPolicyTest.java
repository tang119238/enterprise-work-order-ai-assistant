package com.tangmeng.workorder.analytics;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import static org.assertj.core.api.Assertions.assertThatThrownBy;

class AnalyticsSqlPolicyTest {

    private AnalyticsSqlPolicy policy;

    @BeforeEach
    void setUp() {
        policy = new AnalyticsSqlPolicy();
    }

    @Test
    void simpleSelectPasses() throws Exception {
        policy.validate("SELECT work_order_no, status FROM analytics_work_order_v");
    }

    @Test
    void selectWithWherePasses() throws Exception {
        policy.validate("SELECT work_order_no FROM analytics_work_order_v WHERE status = 'CLOSED'");
    }

    @Test
    void selectWithAggregatePasses() throws Exception {
        policy.validate("SELECT status, COUNT(*) FROM analytics_work_order_v GROUP BY status");
    }

    @Test
    void selectWithJoinPasses() throws Exception {
        policy.validate(
            "SELECT w.work_order_no, q.verdict FROM analytics_work_order_v w " +
            "LEFT JOIN analytics_quality_v q ON w.tenant_id = q.tenant_id"
        );
    }

    @ParameterizedTest
    @ValueSource(strings = {
        "INSERT INTO analytics_work_order_v (status) VALUES ('HACKED')",
        "UPDATE analytics_work_order_v SET status = 'HACKED'",
        "DELETE FROM analytics_work_order_v",
        "DROP TABLE analytics_work_order_v",
        "CREATE TABLE evil (id int)",
    })
    void ddlDmlBlocked(String sql) {
        assertThatThrownBy(() -> policy.validate(sql))
            .isInstanceOf(AnalyticsSqlPolicy.SqlPolicyViolation.class);
    }

    @ParameterizedTest
    @ValueSource(strings = {
        "SELECT * FROM pg_catalog.pg_class",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM pg_stat_activity",
        "SELECT * FROM work_order",
    })
    void systemAndBaseTablesBlocked(String sql) {
        assertThatThrownBy(() -> policy.validate(sql))
            .isInstanceOf(AnalyticsSqlPolicy.SqlPolicyViolation.class);
    }

    @Test
    void emptySqlBlocked() {
        assertThatThrownBy(() -> policy.validate(""))
            .isInstanceOf(AnalyticsSqlPolicy.SqlPolicyViolation.class);
    }

    @Test
    void nullSqlBlocked() {
        assertThatThrownBy(() -> policy.validate(null))
            .isInstanceOf(AnalyticsSqlPolicy.SqlPolicyViolation.class);
    }

    @Test
    void nonSelectBlocked() {
        assertThatThrownBy(() -> policy.validate("CREATE TABLE evil (id int)"))
            .isInstanceOf(AnalyticsSqlPolicy.SqlPolicyViolation.class);
    }

    @Test
    void multiStatementBlocked() {
        assertThatThrownBy(() -> policy.validate("SELECT 1; DROP TABLE work_order"))
            .isInstanceOf(AnalyticsSqlPolicy.SqlPolicyViolation.class);
    }

    @Test
    void lineCommentBlocked() {
        assertThatThrownBy(() -> policy.validate("SELECT 1 -- comment"))
            .isInstanceOf(AnalyticsSqlPolicy.SqlPolicyViolation.class);
    }
}
