package com.tangmeng.workorder.integration;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.NullNode;
import com.tangmeng.workorder.domain.ActionProposalEntity;
import com.tangmeng.workorder.mapper.ActionProposalMapper;
import com.tangmeng.workorder.security.TenantContext;
import com.tangmeng.workorder.tenant.TenantTransaction;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.MountableFile;

import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.util.Set;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

@Testcontainers(disabledWithoutDocker = true)
@SpringBootTest
class ActionProposalMapperIntegrationTest {

    private static final UUID TENANT = UUID.fromString("11111111-1111-1111-1111-111111111111");
    private static final UUID USER = UUID.fromString("00000000-0000-0000-0000-000000009001");
    private static final UUID PROJECT = UUID.fromString("00000000-0000-0000-0000-000000010001");
    private static final UUID PROPOSAL = UUID.fromString("00000000-0000-0000-0000-000000009301");
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
    static void databaseProperties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", () -> "work_order_app");
        registry.add("spring.datasource.password", () -> "work_order_app_dev");
        registry.add("spring.flyway.url", POSTGRES::getJdbcUrl);
        registry.add("spring.flyway.user", () -> "flyway_owner");
        registry.add("spring.flyway.password", () -> "flyway_owner_dev");
    }

    @Autowired
    private ActionProposalMapper mapper;
    @Autowired
    private TenantTransaction transactions;
    @Autowired
    private ObjectMapper objectMapper;

    @BeforeEach
    void seedRequester() throws Exception {
        try (Connection connection = DriverManager.getConnection(
                POSTGRES.getJdbcUrl(), POSTGRES.getUsername(), POSTGRES.getPassword());
             Statement statement = connection.createStatement()) {
            statement.executeUpdate("""
                insert into user_identity (id, issuer, subject, display_name, status)
                values ('00000000-0000-0000-0000-000000009001', 'test', 'mapper-user', 'Mapper User', 'ACTIVE')
                on conflict (id) do nothing
                """);
        }
    }

    @Test
    void insertsAndReadsObjectJsonJsonNullAndSqlNull() {
        TenantContext context = new TenantContext(
            TENANT, USER, "mapper-user", Set.of("DISPATCHER"), Set.of(PROJECT),
            Set.of("work-order:write"), "request", "trace"
        );
        ActionProposalEntity inserted = ActionProposalEntity.builder()
            .id(PROPOSAL)
            .tenantId(TENANT)
            .actionType("CREATE")
            .commandPayload(objectMapper.createObjectNode().put("title", "round-trip"))
            .beforeSnapshot(NullNode.getInstance())
            .afterSnapshot(objectMapper.createObjectNode().put("status", "PENDING_DISPATCH"))
            .riskLevel("MEDIUM")
            .status("PENDING_CONFIRMATION")
            .requestedBy(USER)
            .expectedVersion(0L)
            .expiresAt(LocalDateTime.parse("2026-07-18T12:15:00"))
            .createdAt(LocalDateTime.parse("2026-07-18T12:00:00"))
            .updatedAt(LocalDateTime.parse("2026-07-18T12:00:00"))
            .executionResult(null)
            .build();

        ActionProposalEntity reloaded = transactions.required(context, () -> {
            assertThat(mapper.insert(inserted)).isEqualTo(1);
            return mapper.selectProposalById(TENANT, PROPOSAL);
        });

        assertThat(reloaded.getCommandPayload().get("title").asText()).isEqualTo("round-trip");
        assertThat(reloaded.getBeforeSnapshot().isNull()).isTrue();
        assertThat(reloaded.getAfterSnapshot().get("status").asText()).isEqualTo("PENDING_DISPATCH");
        assertThat(reloaded.getExecutionResult()).isNull();
    }
}
