package com.tangmeng.workorder.integration;

import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class CommandReliabilityMigrationContractTest {

    @Test
    void migrationEnforcesOneOpenAssignmentAndImmutableWorkOrderEvents() throws Exception {
        String sql = Files.readString(Path.of(
            "src/main/resources/db/migration/V6__command_reliability_invariants.sql"));

        String normalized = sql.toLowerCase(java.util.Locale.ROOT);
        assertThat(normalized).contains(
            "unique", "work_order_assignment", "where unassigned_at is null",
            "work_order_event", "before update or delete", "raise exception");
    }

    @Test
    void postgresReliabilityTestsResetMutableStateAndUseRandomMethodOrder() throws Exception {
        String source = Files.readString(Path.of(
            "src/test/java/com/tangmeng/workorder/integration/IdempotencyConcurrencyTest.java"));

        String normalized = source.toLowerCase(java.util.Locale.ROOT);
        assertThat(normalized).contains(
            "@testmethodorder(methodorderer.random.class)",
            "alter table work_order_event disable trigger work_order_event_append_only",
            "delete from work_order_event where tenant_id=",
            "delete from outbox_event where tenant_id=",
            "delete from idempotency_record where tenant_id=",
            "delete from action_proposal where tenant_id=",
            "delete from work_order_assignment where tenant_id=",
            "update work_order set version=0");
    }
}
