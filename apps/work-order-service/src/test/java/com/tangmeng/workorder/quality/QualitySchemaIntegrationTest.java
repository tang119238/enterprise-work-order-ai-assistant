package com.tangmeng.workorder.quality;

import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;

import java.io.IOException;
import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;

class QualitySchemaIntegrationTest {

    @Test
    void migrationDefinesTenantOwnedRectificationAndAppendOnlyReviewSchema() throws IOException {
        ClassPathResource migration = new ClassPathResource(
            "db/migration/V6__quality_rectification.sql"
        );

        assertThat(migration.exists()).isTrue();
        String sql = migration.getContentAsString(StandardCharsets.UTF_8);

        assertThat(sql).contains(
            "CREATE TABLE rectification_case",
            "CREATE TABLE quality_review_event",
            "original_work_order_id UUID NOT NULL",
            "rectification_work_order_id UUID",
            "current_quality_result_id UUID NOT NULL",
            "inspection_round INTEGER NOT NULL",
            "status IN ('PROPOSED', 'RECTIFYING', 'RECHECKING', 'CLOSED')",
            "review_payload JSONB NOT NULL",
            "TIMESTAMPTZ",
            "FOREIGN KEY (tenant_id, original_work_order_id) REFERENCES work_order(tenant_id, id)",
            "FOREIGN KEY (tenant_id, rectification_work_order_id) REFERENCES work_order(tenant_id, id)",
            "UNIQUE (tenant_id, original_work_order_id, inspection_round)",
            "CREATE INDEX idx_rectification_case_tenant_status",
            "CREATE INDEX idx_quality_review_event_tenant_case_created_at"
        );
        assertThat(sql).doesNotContain("work_order_no");
    }

    @Test
    void migrationForcesRlsAndSeparatesJavaOwnershipFromAiRole() throws IOException {
        String sql = new ClassPathResource(
            "db/migration/V6__quality_rectification.sql"
        ).getContentAsString(StandardCharsets.UTF_8);

        for (String table : new String[]{"rectification_case", "quality_review_event"}) {
            assertThat(sql).contains(
                "ALTER TABLE " + table + " ENABLE ROW LEVEL SECURITY",
                "ALTER TABLE " + table + " FORCE ROW LEVEL SECURITY",
                "CREATE POLICY " + table + "_tenant_policy ON " + table,
                "REVOKE ALL PRIVILEGES ON TABLE " + table
                    + " FROM PUBLIC, ai_app, analytics_reader"
            );
        }
        assertThat(sql).contains(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE rectification_case TO work_order_app",
            "GRANT SELECT, INSERT ON TABLE quality_review_event TO work_order_app",
            "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
        );
        assertThat(sql).doesNotContain(
            "GRANT SELECT, INSERT, UPDATE ON TABLE quality_review_event",
            "GRANT UPDATE ON TABLE quality_review_event",
            " TO ai_app"
        );
    }
}
