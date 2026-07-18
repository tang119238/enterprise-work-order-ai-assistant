package com.tangmeng.workorder.integration;

import com.tangmeng.workorder.api.WorkOrderExecutionResponse;
import com.tangmeng.workorder.command.WorkOrderCommandService;
import com.tangmeng.workorder.domain.ActionProposalEntity;
import com.tangmeng.workorder.security.TenantContext;
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
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

import static org.assertj.core.api.Assertions.assertThat;

@Testcontainers(disabledWithoutDocker = true)
@SpringBootTest
class IdempotencyConcurrencyTest {

    private static final UUID TENANT = UUID.fromString("11111111-1111-1111-1111-111111111111");
    private static final UUID USER = UUID.fromString("00000000-0000-0000-0000-000000009001");
    private static final UUID PROJECT = UUID.fromString("00000000-0000-0000-0000-000000010003");
    private static final UUID TARGET = UUID.fromString("00000000-0000-0000-0000-000000000003");
    private static final UUID PROPOSAL = UUID.fromString("00000000-0000-0000-0000-000000009501");
    private static final Path ROLE_BOOTSTRAP = Path.of("../../infra/postgres/init/001_roles.sql").toAbsolutePath();

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:16-alpine")
        .withDatabaseName("workorders")
        .withEnv("FLYWAY_PASSWORD", "flyway_owner_dev")
        .withEnv("WORK_ORDER_DB_PASSWORD", "work_order_app_dev")
        .withEnv("AI_DB_PASSWORD", "ai_app_dev")
        .withEnv("ANALYTICS_DB_PASSWORD", "analytics_reader_dev")
        .withCopyFileToContainer(MountableFile.forHostPath(ROLE_BOOTSTRAP),
            "/docker-entrypoint-initdb.d/001_roles.sql");

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
    private WorkOrderCommandService service;

    @BeforeEach
    void seedAuthorityAndProposal() throws Exception {
        executeAdmin("""
            insert into user_identity (id,issuer,subject,display_name,status)
            values ('00000000-0000-0000-0000-000000009001','http://localhost:9000','human','Human','ACTIVE')
            on conflict (id) do nothing;
            insert into tenant_membership (id,tenant_id,user_identity_id,role,status)
            values ('00000000-0000-0000-0000-000000009101','11111111-1111-1111-1111-111111111111',
                    '00000000-0000-0000-0000-000000009001','DISPATCHER','ACTIVE')
            on conflict (tenant_id,user_identity_id,role) do update set status='ACTIVE';
            insert into project_scope (id,tenant_id,user_identity_id,project_id,status)
            values ('00000000-0000-0000-0000-000000009201','11111111-1111-1111-1111-111111111111',
                    '00000000-0000-0000-0000-000000009001','00000000-0000-0000-0000-000000010003','ACTIVE')
            on conflict (tenant_id,user_identity_id,project_id) do update set status='ACTIVE';
            insert into action_proposal
              (id,tenant_id,action_type,target_id,command_payload,before_snapshot,after_snapshot,
               risk_level,status,requested_by,expected_version,expires_at)
            values ('00000000-0000-0000-0000-000000009501','11111111-1111-1111-1111-111111111111',
                    'UPDATE','00000000-0000-0000-0000-000000000003',
                    '{"target_work_order_no":"WO-20260718-003","title":"concurrent"}'::jsonb,
                    '{}'::jsonb,'{}'::jsonb,'LOW','PENDING_CONFIRMATION',
                    '00000000-0000-0000-0000-000000009001',0,current_timestamp + interval '1 hour')
            on conflict (id) do nothing;
            """);
    }

    @Test
    void simultaneousSameKeyConfirmationsProduceOneFactEventOutboxAndReplayResponse() throws Exception {
        TenantContext context = context();
        ActionProposalEntity reference = ActionProposalEntity.builder().id(PROPOSAL).tenantId(TENANT).build();
        CountDownLatch start = new CountDownLatch(1);
        Callable<WorkOrderExecutionResponse> call = () -> { start.await(); return service.execute(context, reference, "same-key"); };
        var pool = Executors.newFixedThreadPool(2);
        try {
            Future<WorkOrderExecutionResponse> first = pool.submit(call);
            Future<WorkOrderExecutionResponse> second = pool.submit(call);
            start.countDown();
            assertThat(first.get()).isEqualTo(second.get());
        } finally {
            pool.shutdownNow();
        }

        assertThat(queryCount("select count(*) from work_order_event where tenant_id='" + TENANT
            + "' and work_order_id='" + TARGET + "'")).isEqualTo(1);
        assertThat(queryCount("select count(*) from outbox_event where tenant_id='" + TENANT
            + "' and aggregate_id='" + TARGET + "'")).isEqualTo(1);
        assertThat(queryCount("select count(*) from idempotency_record where tenant_id='" + TENANT
            + "' and idempotency_key='same-key'")).isEqualTo(1);
        assertThat(queryCount("select version from work_order where tenant_id='" + TENANT
            + "' and id='" + TARGET + "'")).isEqualTo(1);
    }

    @Test
    void forcedEventInsertFailureRollsBackOrderOutboxAndIdempotencyThenMarksProposalFailed() throws Exception {
        UUID rollbackProposal = UUID.fromString("00000000-0000-0000-0000-000000009502");
        executeAdmin("""
            insert into project_scope (id,tenant_id,user_identity_id,project_id,status)
            values ('00000000-0000-0000-0000-000000009202','11111111-1111-1111-1111-111111111111',
                    '00000000-0000-0000-0000-000000009001','00000000-0000-0000-0000-000000010002','ACTIVE')
            on conflict (tenant_id,user_identity_id,project_id) do update set status='ACTIVE';
            insert into action_proposal
              (id,tenant_id,action_type,target_id,command_payload,before_snapshot,after_snapshot,
               risk_level,status,requested_by,expected_version,expires_at)
            values ('00000000-0000-0000-0000-000000009502','11111111-1111-1111-1111-111111111111',
                    'UPDATE','00000000-0000-0000-0000-000000000002',
                    '{"target_work_order_no":"WO-20260718-002","title":"must rollback"}'::jsonb,
                    '{}'::jsonb,'{}'::jsonb,'LOW','PENDING_CONFIRMATION',
                    '00000000-0000-0000-0000-000000009001',0,current_timestamp + interval '1 hour');
            create or replace function force_work_order_event_failure() returns trigger language plpgsql as $$
            begin raise exception 'forced event failure'; end $$;
            create trigger force_work_order_event_failure before insert on work_order_event
              for each row execute function force_work_order_event_failure();
            """);
        TenantContext context = new TenantContext(TENANT, USER, "human", Set.of("DISPATCHER"),
            Set.of(PROJECT, UUID.fromString("00000000-0000-0000-0000-000000010002")),
            Set.of("work-order:write"), "request", "trace");
        try {
            org.assertj.core.api.Assertions.assertThatThrownBy(() -> service.execute(context,
                ActionProposalEntity.builder().id(rollbackProposal).tenantId(TENANT).build(), "rollback-key"))
                .isInstanceOf(RuntimeException.class);
        } finally {
            executeAdmin("drop trigger if exists force_work_order_event_failure on work_order_event; "
                + "drop function if exists force_work_order_event_failure();");
        }

        assertThat(queryCount("select version from work_order where tenant_id='" + TENANT
            + "' and id='00000000-0000-0000-0000-000000000002'")).isZero();
        assertThat(queryCount("select count(*) from outbox_event where tenant_id='" + TENANT
            + "' and aggregate_id='00000000-0000-0000-0000-000000000002'")).isZero();
        assertThat(queryCount("select count(*) from idempotency_record where tenant_id='" + TENANT
            + "' and idempotency_key='rollback-key'")).isZero();
        assertThat(queryText("select status from action_proposal where id='" + rollbackProposal + "'"))
            .isEqualTo("FAILED");
    }

    private TenantContext context() {
        return new TenantContext(TENANT, USER, "human", Set.of("DISPATCHER"), Set.of(PROJECT),
            Set.of("work-order:write"), "request", "trace");
    }

    private void executeAdmin(String sql) throws Exception {
        try (Connection connection = DriverManager.getConnection(
                POSTGRES.getJdbcUrl(), POSTGRES.getUsername(), POSTGRES.getPassword());
             Statement statement = connection.createStatement()) {
            statement.execute(sql);
        }
    }

    private long queryCount(String sql) throws Exception {
        try (Connection connection = DriverManager.getConnection(
                POSTGRES.getJdbcUrl(), POSTGRES.getUsername(), POSTGRES.getPassword());
             Statement statement = connection.createStatement(); ResultSet result = statement.executeQuery(sql)) {
            result.next();
            return result.getLong(1);
        }
    }

    private String queryText(String sql) throws Exception {
        try (Connection connection = DriverManager.getConnection(
                POSTGRES.getJdbcUrl(), POSTGRES.getUsername(), POSTGRES.getPassword());
             Statement statement = connection.createStatement(); ResultSet result = statement.executeQuery(sql)) {
            result.next();
            return result.getString(1);
        }
    }
}
