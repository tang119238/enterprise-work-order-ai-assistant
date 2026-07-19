package com.tangmeng.workorder.integration;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.MountableFile;

import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.List;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@Testcontainers(disabledWithoutDocker = true)
@SpringBootTest
class TenantSchemaIntegrationTest {

    private static final List<String> TENANT_SCOPED_TABLES = Stream.concat(
        MigrationContractTest.TENANT_SCOPED_TABLES.stream(),
        MigrationContractTest.QUALITY_TENANT_SCOPED_TABLES.stream()
    ).toList();

    private static final String TENANT_A = "11111111-1111-1111-1111-111111111111";
    private static final String TENANT_B = "22222222-2222-2222-2222-222222222222";
    private static final String USER_ID = "00000000-0000-0000-0000-000000009001";
    private static final String WORK_ORDER_B = "00000000-0000-0000-0000-000000000026";
    private static final String PROJECT_B = "00000000-0000-0000-0000-000000020001";
    private static final Path ROLE_BOOTSTRAP =
        Path.of("../../infra/postgres/init/001_roles.sql").toAbsolutePath();

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:16-alpine")
        .withDatabaseName("workorders")
        .withEnv("FLYWAY_PASSWORD", "flyway_owner_dev")
        .withEnv("WORK_ORDER_DB_PASSWORD", "work_order_app_dev")
        .withEnv("AI_DB_PASSWORD", "ai_app_dev")
        .withEnv("ANALYTICS_DB_PASSWORD", "analytics_reader_dev")
        .withCopyFileToContainer(
            MountableFile.forHostPath(ROLE_BOOTSTRAP),
            "/docker-entrypoint-initdb.d/001_roles.sql"
        );

    @DynamicPropertySource
    static void applicationUsesSeparatedProductionRoles(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", () -> "work_order_app");
        registry.add("spring.datasource.password", () -> "work_order_app_dev");
        registry.add("spring.flyway.url", POSTGRES::getJdbcUrl);
        registry.add("spring.flyway.user", () -> "flyway_owner");
        registry.add("spring.flyway.password", () -> "flyway_owner_dev");
    }

    @Autowired
    private JdbcTemplate jdbc;

    @BeforeEach
    void seedTenantFixtures() throws SQLException {
        try (Connection connection = adminConnection(); Statement statement = connection.createStatement()) {
            statement.executeUpdate("""
                insert into user_identity (id, issuer, subject, display_name, status)
                values ('00000000-0000-0000-0000-000000009001', 'test-issuer', 'rls-user', 'RLS User', 'ACTIVE')
                on conflict (id) do nothing
                """);
            statement.executeUpdate("""
                insert into tenant_membership (id, tenant_id, user_identity_id, role, status)
                values ('00000000-0000-0000-0000-000000009101', '11111111-1111-1111-1111-111111111111',
                    '00000000-0000-0000-0000-000000009001', 'OPERATOR', 'ACTIVE')
                on conflict (id) do nothing
                """);
            statement.executeUpdate("""
                insert into project_scope (id, tenant_id, user_identity_id, project_id, status)
                values ('00000000-0000-0000-0000-000000009102', '11111111-1111-1111-1111-111111111111',
                    '00000000-0000-0000-0000-000000009001', '00000000-0000-0000-0000-000000010001', 'ACTIVE')
                on conflict (id) do nothing
                """);
            statement.executeUpdate("""
                insert into action_proposal (id, tenant_id, action_type, command_payload, before_snapshot, after_snapshot,
                    risk_level, status, requested_by, expires_at)
                values ('00000000-0000-0000-0000-000000009103', '11111111-1111-1111-1111-111111111111', 'UPDATE',
                    '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, 'LOW', 'PENDING_CONFIRMATION',
                    '00000000-0000-0000-0000-000000009001', current_timestamp + interval '1 hour')
                on conflict (id) do nothing
                """);
            statement.executeUpdate("""
                insert into work_order_assignment (id, tenant_id, work_order_id, assignee_id, assigned_at, reason)
                values ('00000000-0000-0000-0000-000000009104', '11111111-1111-1111-1111-111111111111',
                    '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000009001', current_timestamp, 'fixture')
                on conflict (id) do nothing
                """);
            statement.executeUpdate("""
                insert into work_order_event (id, tenant_id, work_order_id, event_type, command_type, before_snapshot,
                    after_snapshot, actor_id, request_id, trace_id)
                values ('00000000-0000-0000-0000-000000009105', '11111111-1111-1111-1111-111111111111',
                    '00000000-0000-0000-0000-000000000001', 'FIXTURE', 'FIXTURE', '{}'::jsonb, '{}'::jsonb,
                    '00000000-0000-0000-0000-000000009001', 'fixture-request', 'fixture-trace')
                on conflict (id) do nothing
                """);
            statement.executeUpdate("""
                insert into idempotency_record (id, tenant_id, operation, idempotency_key, request_hash)
                values ('00000000-0000-0000-0000-000000009106', '11111111-1111-1111-1111-111111111111',
                    'FIXTURE', 'fixture-key', 'fixture-hash')
                on conflict (id) do nothing
                """);
            statement.executeUpdate("""
                insert into outbox_event (id, tenant_id, aggregate_id, aggregate_type, event_type, payload)
                values ('00000000-0000-0000-0000-000000009107', '11111111-1111-1111-1111-111111111111',
                    '00000000-0000-0000-0000-000000000001', 'WORK_ORDER', 'FIXTURE', '{}'::jsonb)
                on conflict (id) do nothing
                """);
            statement.executeUpdate("""
                insert into inbox_message (id, tenant_id, provider, external_message_id, message_type, payload)
                values ('00000000-0000-0000-0000-000000009108', '11111111-1111-1111-1111-111111111111',
                    'fixture', 'fixture-message', 'FIXTURE', '{}'::jsonb)
                on conflict (id) do nothing
                """);
            statement.executeUpdate("""
                insert into rectification_case (id, tenant_id, original_work_order_id, current_quality_result_id,
                    inspection_round, status)
                values ('00000000-0000-0000-0000-000000009109', '22222222-2222-2222-2222-222222222222',
                    '00000000-0000-0000-0000-000000000026', '00000000-0000-0000-0000-000000009301', 1, 'PROPOSED')
                on conflict (tenant_id, id) do nothing
                """);
            statement.executeUpdate("""
                insert into quality_review_event (id, tenant_id, rectification_case_id, quality_result_id, decision,
                    previous_verdict, reviewed_verdict, reason, actor_id)
                values ('00000000-0000-0000-0000-000000009110', '22222222-2222-2222-2222-222222222222',
                    '00000000-0000-0000-0000-000000009109', '00000000-0000-0000-0000-000000009301', 'ACCEPT',
                    'PASS', 'PASS', 'fixture', '00000000-0000-0000-0000-000000009001')
                on conflict (tenant_id, id) do nothing
                """);
        }
    }

    @Test
    void migratedSeedHasTwoIsolatedTenants() {
        assertThat(jdbc.queryForObject("select count(*) from tenant", Long.class)).isEqualTo(2L);

        jdbc.execute((ConnectionCallback<Void>) connection -> {
            try {
                setTenant(connection, TENANT_A);
                assertThat(count(connection, "select count(*) from work_order")).isEqualTo(25L);
                setTenant(connection, TENANT_B);
                assertThat(count(connection, "select count(*) from work_order")).isEqualTo(25L);
                return null;
            } finally {
                clearTenant(connection);
            }
        });
    }

    @Test
    void everyTenantTableCarriesTenantId() {
        for (String table : TENANT_SCOPED_TABLES) {
            Integer count = jdbc.queryForObject("""
                    select count(*) from information_schema.columns
                    where table_name=? and column_name='tenant_id'
                    """, Integer.class, table);
            assertThat(count).isEqualTo(1);
        }
    }

    @Test
    void migratedSeedPreservesAllFiveReworkChains() {
        jdbc.execute((ConnectionCallback<Void>) connection -> {
            try {
                setTenant(connection, TENANT_A);
                long tenantAReworkCount = count(connection,
                    "select count(*) from work_order where root_work_order_id is not null");
                setTenant(connection, TENANT_B);
                long tenantBReworkCount = count(connection,
                    "select count(*) from work_order where root_work_order_id is not null");
                assertThat(tenantAReworkCount + tenantBReworkCount).isEqualTo(5L);
                return null;
            } finally {
                clearTenant(connection);
            }
        });
    }

    @Test
    void tenantTablesForceRlsWithMatchingPoliciesAndSeparatedRoles() {
        try (Connection connection = adminConnection()) {
            for (String table : TENANT_SCOPED_TABLES) {
                try (PreparedStatement statement = connection.prepareStatement("""
                        select c.relrowsecurity, c.relforcerowsecurity, pg_get_userbyid(c.relowner),
                               pg_get_expr(p.polqual, p.polrelid), pg_get_expr(p.polwithcheck, p.polrelid)
                        from pg_class c
                        join pg_namespace n on n.oid = c.relnamespace
                        join pg_policy p on p.polrelid = c.oid
                        where n.nspname = 'public' and c.relname = ? and p.polname = ?
                        """)) {
                    statement.setString(1, table);
                    statement.setString(2, table + "_tenant_policy");
                    try (ResultSet result = statement.executeQuery()) {
                        assertThat(result.next()).isTrue();
                        assertThat(result.getBoolean(1)).isTrue();
                        assertThat(result.getBoolean(2)).isTrue();
                        assertThat(result.getString(3)).isEqualTo("flyway_owner");
                        assertThat(result.getString(4)).contains("tenant_id", "current_setting", "app.tenant_id");
                        assertThat(result.getString(5)).isEqualTo(result.getString(4));
                    }
                }
            }
            try (PreparedStatement statement = connection.prepareStatement("""
                    select rolname, rolbypassrls from pg_roles
                    where rolname in ('flyway_owner', 'work_order_app') order by rolname
                    """);
                 ResultSet result = statement.executeQuery()) {
                assertThat(result.next()).isTrue();
                assertThat(result.getString(1)).isEqualTo("flyway_owner");
                assertThat(result.getBoolean(2)).isFalse();
                assertThat(result.next()).isTrue();
                assertThat(result.getString(1)).isEqualTo("work_order_app");
                assertThat(result.getBoolean(2)).isFalse();
            }
        } catch (SQLException exception) {
            throw new AssertionError("could not inspect tenant RLS metadata", exception);
        }
    }

    @Test
    void applicationRoleCannotReadWithoutTenantContextOrWriteAcrossTenants() {
        jdbc.execute((ConnectionCallback<Void>) connection -> {
            try {
                assertThat(currentUser(connection)).isEqualTo("work_order_app");
                clearTenant(connection);
                for (String table : TENANT_SCOPED_TABLES) {
                    assertThat(count(connection, "select count(*) from " + table)).isZero();
                }

                setTenant(connection, TENANT_A);
                for (String table : TENANT_SCOPED_TABLES) {
                    assertThatThrownBy(() -> insertForTenantB(connection, table))
                        .isInstanceOf(SQLException.class)
                        .hasMessageContaining("row-level security policy");
                }
                return null;
            } finally {
                clearTenant(connection);
            }
        });
    }

    private static Connection adminConnection() throws SQLException {
        return DriverManager.getConnection(POSTGRES.getJdbcUrl(), POSTGRES.getUsername(), POSTGRES.getPassword());
    }

    private static void setTenant(Connection connection, String tenantId) throws SQLException {
        try (PreparedStatement statement = connection.prepareStatement(
                "select set_config('app.tenant_id', ?, false)")) {
            statement.setString(1, tenantId);
            statement.execute();
        }
    }

    private static void clearTenant(Connection connection) throws SQLException {
        setTenant(connection, "");
    }

    private static long count(Connection connection, String sql) throws SQLException {
        try (Statement statement = connection.createStatement(); ResultSet result = statement.executeQuery(sql)) {
            assertThat(result.next()).isTrue();
            return result.getLong(1);
        }
    }

    private static String currentUser(Connection connection) throws SQLException {
        try (Statement statement = connection.createStatement(); ResultSet result = statement.executeQuery("select current_user")) {
            assertThat(result.next()).isTrue();
            return result.getString(1);
        }
    }

    private static void insertForTenantB(Connection connection, String table) throws SQLException {
        String sql = switch (table) {
            case "tenant_membership" -> """
                insert into tenant_membership (id, tenant_id, user_identity_id, role, status)
                values ('00000000-0000-0000-0000-000000009201', '%s', '%s', 'OPERATOR', 'ACTIVE')
                """.formatted(TENANT_B, USER_ID);
            case "project" -> """
                insert into project (id, tenant_id, project_key, name, status)
                values ('00000000-0000-0000-0000-000000009202', '%s', 'cross-tenant-project', 'Cross Tenant', 'ACTIVE')
                """.formatted(TENANT_B);
            case "project_scope" -> """
                insert into project_scope (id, tenant_id, user_identity_id, project_id, status)
                values ('00000000-0000-0000-0000-000000009203', '%s', '%s', '%s', 'ACTIVE')
                """.formatted(TENANT_B, USER_ID, PROJECT_B);
            case "work_order" -> """
                insert into work_order (id, tenant_id, work_order_no, title, description, project_id, project_name,
                    space_path, order_type, priority, status, source, created_at, due_at)
                values ('00000000-0000-0000-0000-000000009204', '%s', 'WO-CROSS-TENANT', 'Cross tenant', 'fixture',
                    '%s', '星河中心', 'fixture', 'STANDARD', 'LOW', 'PENDING_DISPATCH', 'FIXTURE',
                    current_timestamp, current_timestamp + interval '1 hour')
                """.formatted(TENANT_B, PROJECT_B);
            case "action_proposal" -> """
                insert into action_proposal (id, tenant_id, action_type, command_payload, before_snapshot, after_snapshot,
                    risk_level, status, requested_by, expires_at)
                values ('00000000-0000-0000-0000-000000009205', '%s', 'UPDATE', '{}'::jsonb, '{}'::jsonb, '{}'::jsonb,
                    'LOW', 'PENDING_CONFIRMATION', '%s', current_timestamp + interval '1 hour')
                """.formatted(TENANT_B, USER_ID);
            case "work_order_assignment" -> """
                insert into work_order_assignment (id, tenant_id, work_order_id, assignee_id, assigned_at, reason)
                values ('00000000-0000-0000-0000-000000009206', '%s', '%s', '%s', current_timestamp, 'cross tenant')
                """.formatted(TENANT_B, WORK_ORDER_B, USER_ID);
            case "work_order_event" -> """
                insert into work_order_event (id, tenant_id, work_order_id, event_type, command_type, before_snapshot,
                    after_snapshot, actor_id, request_id, trace_id)
                values ('00000000-0000-0000-0000-000000009207', '%s', '%s', 'CROSS', 'CROSS', '{}'::jsonb, '{}'::jsonb,
                    '%s', 'cross-request', 'cross-trace')
                """.formatted(TENANT_B, WORK_ORDER_B, USER_ID);
            case "idempotency_record" -> """
                insert into idempotency_record (id, tenant_id, operation, idempotency_key, request_hash)
                values ('00000000-0000-0000-0000-000000009208', '%s', 'CROSS', 'cross-key', 'cross-hash')
                """.formatted(TENANT_B);
            case "outbox_event" -> """
                insert into outbox_event (id, tenant_id, aggregate_id, aggregate_type, event_type, payload)
                values ('00000000-0000-0000-0000-000000009209', '%s', '%s', 'WORK_ORDER', 'CROSS', '{}'::jsonb)
                """.formatted(TENANT_B, WORK_ORDER_B);
            case "inbox_message" -> """
                insert into inbox_message (id, tenant_id, provider, external_message_id, message_type, payload)
                values ('00000000-0000-0000-0000-000000009210', '%s', 'cross', 'cross-message', 'CROSS', '{}'::jsonb)
                """.formatted(TENANT_B);
            case "rectification_case" -> """
                insert into rectification_case (id, tenant_id, original_work_order_id, current_quality_result_id,
                    inspection_round, status)
                values ('00000000-0000-0000-0000-000000009211', '%s', '%s',
                    '00000000-0000-0000-0000-000000009302', 2, 'PROPOSED')
                """.formatted(TENANT_B, WORK_ORDER_B);
            case "quality_review_event" -> """
                insert into quality_review_event (id, tenant_id, rectification_case_id, quality_result_id, decision,
                    previous_verdict, reviewed_verdict, reason, actor_id)
                values ('00000000-0000-0000-0000-000000009212', '%s',
                    '00000000-0000-0000-0000-000000009109', '00000000-0000-0000-0000-000000009301',
                    'ACCEPT', 'PASS', 'PASS', 'cross tenant', '%s')
                """.formatted(TENANT_B, USER_ID);
            default -> throw new IllegalArgumentException("unknown tenant table: " + table);
        };
        try (Statement statement = connection.createStatement()) {
            statement.executeUpdate(sql);
        }
    }
}
