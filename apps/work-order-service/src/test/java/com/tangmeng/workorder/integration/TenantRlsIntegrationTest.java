package com.tangmeng.workorder.integration;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.MountableFile;

import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@Testcontainers(disabledWithoutDocker = true)
@SpringBootTest
@AutoConfigureMockMvc
class TenantRlsIntegrationTest {

    private static final String TENANT_A = "11111111-1111-1111-1111-111111111111";
    private static final String TENANT_B = "22222222-2222-2222-2222-222222222222";
    private static final String USER_ID = "00000000-0000-0000-0000-000000009301";
    private static final List<String> PROJECTS_A = List.of(
        "00000000-0000-0000-0000-000000010001",
        "00000000-0000-0000-0000-000000010002",
        "00000000-0000-0000-0000-000000010003"
    );
    private static final List<String> PROJECTS_B = List.of(
        "00000000-0000-0000-0000-000000020001",
        "00000000-0000-0000-0000-000000020002",
        "00000000-0000-0000-0000-000000020003"
    );
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
    static void productionRoleAndIssuer(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", () -> "work_order_app");
        registry.add("spring.datasource.password", () -> "work_order_app_dev");
        registry.add("spring.flyway.url", POSTGRES::getJdbcUrl);
        registry.add("spring.flyway.user", () -> "flyway_owner");
        registry.add("spring.flyway.password", () -> "flyway_owner_dev");
        registry.add("security.jwt.issuer-uri", () -> "test-issuer");
    }

    @Autowired
    private MockMvc mvc;

    @MockitoBean
    private JwtDecoder jwtDecoder;

    @BeforeEach
    void seedVerifiedMembershipsAndDistinctTenantTotal() throws SQLException {
        try (Connection connection = adminConnection(); Statement statement = connection.createStatement()) {
            statement.executeUpdate("""
                insert into user_identity (id, issuer, subject, display_name, status)
                values ('00000000-0000-0000-0000-000000009301', 'test-issuer', 'read-user', 'Read User', 'ACTIVE')
                on conflict (id) do nothing
                """);
            statement.executeUpdate("""
                insert into tenant_membership (id, tenant_id, user_identity_id, role, status)
                values
                    ('00000000-0000-0000-0000-000000009311', '11111111-1111-1111-1111-111111111111',
                        '00000000-0000-0000-0000-000000009301', 'DISPATCHER', 'ACTIVE'),
                    ('00000000-0000-0000-0000-000000009312', '22222222-2222-2222-2222-222222222222',
                        '00000000-0000-0000-0000-000000009301', 'DISPATCHER', 'ACTIVE')
                on conflict (id) do nothing
                """);
            statement.executeUpdate("""
                insert into project_scope (id, tenant_id, user_identity_id, project_id, status)
                values
                    ('00000000-0000-0000-0000-000000009321', '11111111-1111-1111-1111-111111111111',
                        '00000000-0000-0000-0000-000000009301', '00000000-0000-0000-0000-000000010001', 'ACTIVE'),
                    ('00000000-0000-0000-0000-000000009322', '11111111-1111-1111-1111-111111111111',
                        '00000000-0000-0000-0000-000000009301', '00000000-0000-0000-0000-000000010002', 'ACTIVE'),
                    ('00000000-0000-0000-0000-000000009323', '11111111-1111-1111-1111-111111111111',
                        '00000000-0000-0000-0000-000000009301', '00000000-0000-0000-0000-000000010003', 'ACTIVE'),
                    ('00000000-0000-0000-0000-000000009324', '22222222-2222-2222-2222-222222222222',
                        '00000000-0000-0000-0000-000000009301', '00000000-0000-0000-0000-000000020001', 'ACTIVE'),
                    ('00000000-0000-0000-0000-000000009325', '22222222-2222-2222-2222-222222222222',
                        '00000000-0000-0000-0000-000000009301', '00000000-0000-0000-0000-000000020002', 'ACTIVE'),
                    ('00000000-0000-0000-0000-000000009326', '22222222-2222-2222-2222-222222222222',
                        '00000000-0000-0000-0000-000000009301', '00000000-0000-0000-0000-000000020003', 'ACTIVE')
                on conflict (id) do nothing
                """);
            statement.execute("""
                select set_config('app.tenant_id', '22222222-2222-2222-2222-222222222222', false)
                """);
            statement.executeUpdate("""
                insert into work_order (id, tenant_id, work_order_no, title, description, project_id, project_name,
                    space_path, order_type, priority, status, source, created_at, due_at)
                values ('00000000-0000-0000-0000-000000009399', '22222222-2222-2222-2222-222222222222',
                    'WO-TENANT-B-EXTRA', 'Tenant B extra', 'isolation fixture',
                    '00000000-0000-0000-0000-000000020001', '星河中心', 'fixture', 'STANDARD', 'LOW',
                    'PENDING_DISPATCH', 'FIXTURE', current_timestamp, current_timestamp + interval '1 hour')
                on conflict (id) do nothing
                """);
        }
        when(jwtDecoder.decode("tenant-a")).thenReturn(jwt("tenant-a", TENANT_A, PROJECTS_A));
        when(jwtDecoder.decode("tenant-b")).thenReturn(jwt("tenant-b", TENANT_B, PROJECTS_B));
    }

    @Test
    void twoVerifiedJwtsIsolateDetailAndPageTotals() throws Exception {
        mvc.perform(get("/api/work-orders/WO-20260718-026")
                .header("Authorization", "Bearer tenant-a"))
            .andExpect(status().isNotFound())
            .andExpect(jsonPath("$.code").value("WORK_ORDER_NOT_FOUND"));

        mvc.perform(get("/api/work-orders")
                .header("Authorization", "Bearer tenant-a")
                .param("size", "100"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.total").value(25));

        mvc.perform(get("/api/work-orders")
                .header("Authorization", "Bearer tenant-b")
                .param("size", "100"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.total").value(26));
    }

    @Test
    void tenantAReworkChainCannotIncludeTenantBRows() throws Exception {
        mvc.perform(get("/api/work-orders/WO-20260718-028/rework-chain")
                .header("Authorization", "Bearer tenant-a"))
            .andExpect(status().isNotFound());

        mvc.perform(get("/api/work-orders/WO-20260718-028/rework-chain")
                .header("Authorization", "Bearer tenant-b"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$[0].work_order_no").value("WO-20260718-027"))
            .andExpect(jsonPath("$[1].work_order_no").value("WO-20260718-028"));
    }

    @Test
    void runtimeRoleWithoutTenantContextSeesNoWorkOrders() throws SQLException {
        try (Connection connection = DriverManager.getConnection(
                POSTGRES.getJdbcUrl(), "work_order_app", "work_order_app_dev");
             Statement statement = connection.createStatement();
             ResultSet result = statement.executeQuery("select count(*) from work_order")) {
            assertThat(result.next()).isTrue();
            assertThat(result.getLong(1)).isZero();
        }
    }

    private static Connection adminConnection() throws SQLException {
        return DriverManager.getConnection(POSTGRES.getJdbcUrl(), POSTGRES.getUsername(), POSTGRES.getPassword());
    }

    private static Jwt jwt(String token, String tenantId, List<String> projectIds) {
        return Jwt.withTokenValue(token)
            .header("alg", "RS256")
            .claim("iss", "test-issuer")
            .claim("aud", List.of("work-order-service"))
            .claim("sub", "read-user")
            .claim("tenant_id", tenantId)
            .claim("roles", List.of("DISPATCHER"))
            .claim("project_ids", projectIds)
            .claim("scope", "work-order:read")
            .build();
    }
}
