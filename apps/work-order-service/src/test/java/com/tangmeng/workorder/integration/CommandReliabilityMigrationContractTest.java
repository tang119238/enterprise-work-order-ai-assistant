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
}
