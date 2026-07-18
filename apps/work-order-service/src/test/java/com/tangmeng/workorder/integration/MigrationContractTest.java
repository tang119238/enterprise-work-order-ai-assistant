package com.tangmeng.workorder.integration;

import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class MigrationContractTest {

    @Test
    void migrationsCreateSchemaAndExactlyFiftyDeterministicRows() throws IOException {
        ClassPathResource schema = new ClassPathResource(
            "db/migration/V1__create_work_orders.sql"
        );
        ClassPathResource seed = new ClassPathResource(
            "db/migration/V2__seed_synthetic_work_orders.sql"
        );

        assertThat(schema.exists()).isTrue();
        assertThat(seed.exists()).isTrue();

        String schemaSql = schema.getContentAsString(StandardCharsets.UTF_8);
        String seedSql = seed.getContentAsString(StandardCharsets.UTF_8);
        assertThat(schemaSql).contains("CREATE TABLE work_order", "root_work_order_no");
        assertThat(seedSql)
            .contains("generate_series(1, 50)")
            .contains("ARRAY[8, 18, 28, 38, 48]")
            .contains("ON CONFLICT (work_order_no) DO NOTHING");
    }

    @Test
    void multitenantMigrationsDefineTenantSchemaRlsAndSyntheticSplit() throws IOException {
        ClassPathResource schema = new ClassPathResource(
            "db/migration/V3__multitenant_work_order_schema.sql"
        );
        ClassPathResource rls = new ClassPathResource(
            "db/migration/V4__enable_tenant_rls.sql"
        );
        ClassPathResource seedSplit = new ClassPathResource(
            "db/migration/V5__split_synthetic_tenants.sql"
        );

        assertThat(schema.exists()).isTrue();
        assertThat(rls.exists()).isTrue();
        assertThat(seedSplit.exists()).isTrue();

        String schemaSql = schema.getContentAsString(StandardCharsets.UTF_8);
        String rlsSql = rls.getContentAsString(StandardCharsets.UTF_8);
        String seedSplitSql = seedSplit.getContentAsString(StandardCharsets.UTF_8);

        assertThat(schemaSql).contains(
            "CREATE TABLE tenant",
            "CREATE TABLE user_identity",
            "CREATE TABLE tenant_membership",
            "CREATE TABLE project_scope",
            "CREATE TABLE project",
            "CREATE TABLE action_proposal",
            "CREATE TABLE work_order_assignment",
            "CREATE TABLE work_order_event",
            "CREATE TABLE idempotency_record",
            "CREATE TABLE outbox_event",
            "CREATE TABLE inbox_message",
            "CONSTRAINT uq_work_order_tenant_work_order_no UNIQUE (tenant_id, work_order_no)",
            "version BIGINT NOT NULL DEFAULT 0",
            "accepted_at TIMESTAMP"
        );
        for (String table : List.of("work_order", "action_proposal", "work_order_event",
                "work_order_assignment", "idempotency_record", "outbox_event", "inbox_message")) {
            assertThat(rlsSql).contains(
                "ALTER TABLE " + table + " ENABLE ROW LEVEL SECURITY",
                "ALTER TABLE " + table + " FORCE ROW LEVEL SECURITY",
                "CREATE POLICY " + table + "_tenant_policy ON " + table
            );
        }
        assertThat(rlsSql).contains(
            "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
        );
        assertThat(seedSplitSql).contains(
            "WO-20260718-025",
            "WO-20260718-026",
            "root_work_order_id"
        );
    }
}
