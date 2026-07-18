package com.tangmeng.workorder.integration;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@Testcontainers(disabledWithoutDocker = true)
@SpringBootTest
class TenantSchemaIntegrationTest {

    @Container
    @ServiceConnection
    static final PostgreSQLContainer<?> POSTGRES =
        new PostgreSQLContainer<>("postgres:16-alpine");

    @DynamicPropertySource
    static void flywayUsesTheTestcontainerOwner(DynamicPropertyRegistry registry) {
        registry.add("spring.flyway.user", POSTGRES::getUsername);
        registry.add("spring.flyway.password", POSTGRES::getPassword);
    }

    @Autowired
    private JdbcTemplate jdbc;

    @Test
    void migratedSeedHasTwoIsolatedTenants() {
        assertThat(jdbc.queryForObject("select count(*) from tenant", Long.class)).isEqualTo(2L);
        assertThat(jdbc.queryForList("select tenant_id, count(*) c from work_order group by tenant_id"))
            .extracting(row -> ((Number) row.get("c")).longValue())
            .containsExactlyInAnyOrder(25L, 25L);
    }

    @Test
    void everyTenantTableCarriesTenantId() {
        for (String table : List.of("work_order", "action_proposal", "work_order_event",
                "work_order_assignment", "idempotency_record", "outbox_event", "inbox_message")) {
            Integer count = jdbc.queryForObject("""
                    select count(*) from information_schema.columns
                    where table_name=? and column_name='tenant_id'
                    """, Integer.class, table);
            assertThat(count).isEqualTo(1);
        }
    }

    @Test
    void migratedSeedPreservesAllFiveReworkChains() {
        Long reworkCount = jdbc.queryForObject(
            "select count(*) from work_order where root_work_order_id is not null", Long.class
        );

        assertThat(reworkCount).isEqualTo(5L);
    }

    @Test
    void rowLevelSecurityFailsClosedWithoutTenantContext() {
        jdbc.execute("create role tenant_rls_test nologin");
        jdbc.execute("grant select on work_order to tenant_rls_test");
        jdbc.execute("set role tenant_rls_test");
        try {
            assertThat(jdbc.queryForObject("select count(*) from work_order", Long.class)).isZero();

            jdbc.queryForObject(
                "select set_config('app.tenant_id', '11111111-1111-1111-1111-111111111111', false)",
                String.class
            );
            assertThat(jdbc.queryForObject("select count(*) from work_order", Long.class)).isEqualTo(25L);
        } finally {
            jdbc.execute("reset role");
            jdbc.queryForObject("select set_config('app.tenant_id', '', false)", String.class);
        }
    }
}
