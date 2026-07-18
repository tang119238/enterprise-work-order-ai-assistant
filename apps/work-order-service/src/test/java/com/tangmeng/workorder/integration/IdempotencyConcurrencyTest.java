package com.tangmeng.workorder.integration;

import com.tangmeng.workorder.api.WorkOrderExecutionResponse;
import com.tangmeng.workorder.command.WorkOrderCommandService;
import com.tangmeng.workorder.command.ActionNotPermittedException;
import com.tangmeng.workorder.command.IdempotencyConflictException;
import com.tangmeng.workorder.command.InvalidCommandException;
import com.tangmeng.workorder.command.WorkOrderVersionConflictException;
import com.tangmeng.workorder.domain.ActionProposalEntity;
import com.fasterxml.jackson.databind.ObjectMapper;
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
import java.util.concurrent.ExecutionException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

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
    @Autowired
    private ObjectMapper objectMapper;

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
            insert into project_scope (id,tenant_id,user_identity_id,project_id,status)
            values
              ('00000000-0000-0000-0000-000000009202','11111111-1111-1111-1111-111111111111',
               '00000000-0000-0000-0000-000000009001','00000000-0000-0000-0000-000000010001','ACTIVE'),
              ('00000000-0000-0000-0000-000000009203','11111111-1111-1111-1111-111111111111',
               '00000000-0000-0000-0000-000000009001','00000000-0000-0000-0000-000000010002','ACTIVE')
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
        assertThat(queryText("select confirmed_by::text from action_proposal where id='" + rollbackProposal + "'"))
            .isEqualTo(USER.toString());
    }

    @Test
    void deniedConfirmationLeavesProposalAndIdempotencyUnchanged() throws Exception {
        UUID proposal = UUID.fromString("00000000-0000-0000-0000-000000009503");
        seedUpdateProposal(proposal, TARGET, "WO-20260718-003", 0, "PENDING_CONFIRMATION", "1 hour");
        executeAdmin("update tenant_membership set status='INACTIVE' where tenant_id='" + TENANT
            + "' and user_identity_id='" + USER + "'");
        try {
            assertThatThrownBy(() -> service.execute(context(), reference(proposal), "denied-key"))
                .isInstanceOf(ActionNotPermittedException.class);
        } finally {
            executeAdmin("update tenant_membership set status='ACTIVE' where tenant_id='" + TENANT
                + "' and user_identity_id='" + USER + "'");
        }
        assertThat(queryText("select status from action_proposal where id='" + proposal + "'"))
            .isEqualTo("PENDING_CONFIRMATION");
        assertThat(queryCount("select count(*) from idempotency_record where idempotency_key='denied-key'"))
            .isZero();
    }

    @Test
    void completedReplayIsDeniedAfterRoleRevocation() throws Exception {
        UUID proposal = UUID.fromString("00000000-0000-0000-0000-000000009504");
        seedUpdateProposal(proposal, TARGET, "WO-20260718-003", 0, "PENDING_CONFIRMATION", "1 hour");
        WorkOrderExecutionResponse executed = service.execute(context(), reference(proposal), "replay-key");
        executeAdmin("update tenant_membership set status='INACTIVE' where tenant_id='" + TENANT
            + "' and user_identity_id='" + USER + "'");
        try {
            assertThatThrownBy(() -> service.execute(context(), reference(proposal), "replay-key"))
                .isInstanceOf(ActionNotPermittedException.class);
        } finally {
            executeAdmin("update tenant_membership set status='ACTIVE' where tenant_id='" + TENANT
                + "' and user_identity_id='" + USER + "'");
        }
        assertThat(executed.proposalId()).isEqualTo(proposal);
    }

    @Test
    void committedIncompleteIdempotencyRecordIsExplicitlyRejected() throws Exception {
        UUID proposal = UUID.fromString("00000000-0000-0000-0000-000000009505");
        seedUpdateProposal(proposal, TARGET, "WO-20260718-003", 0, "PENDING_CONFIRMATION", "1 hour");
        String hash = WorkOrderCommandService.requestHash(objectMapper, proposal, null);
        executeAdmin("""
            insert into idempotency_record
              (id,tenant_id,operation,idempotency_key,request_hash,created_at)
            values ('00000000-0000-0000-0000-000000009805','11111111-1111-1111-1111-111111111111',
                    'CONFIRM_ACTION_PROPOSAL','incomplete-key','%s',current_timestamp)
            """.formatted(hash));

        assertThatThrownBy(() -> service.execute(context(), reference(proposal), "incomplete-key"))
            .isInstanceOf(InvalidCommandException.class);
        assertThat(queryText("select status from action_proposal where id='" + proposal + "'"))
            .isEqualTo("PENDING_CONFIRMATION");
    }

    @Test
    void repeatedExpiredAttemptsRemainGoneAndNeverBecomeFailed() throws Exception {
        UUID proposal = UUID.fromString("00000000-0000-0000-0000-000000009506");
        seedUpdateProposal(proposal, TARGET, "WO-20260718-003", 0, "CONFIRMED", "-1 minute");

        for (String key : Set.of("expired-real-1", "expired-real-2")) {
            assertThatThrownBy(() -> service.execute(context(), reference(proposal), key))
                .isInstanceOf(com.tangmeng.workorder.command.ActionProposalExpiredException.class);
        }
        assertThat(queryText("select status from action_proposal where id='" + proposal + "'"))
            .isEqualTo("EXPIRED");
    }

    @Test
    void duplicateCreateConflictContainsFreshDatabaseRow() throws Exception {
        UUID proposal = UUID.fromString("00000000-0000-0000-0000-000000009507");
        UUID newId = UUID.fromString("00000000-0000-0000-0000-000000009707");
        executeAdmin("""
            insert into action_proposal
              (id,tenant_id,action_type,command_payload,before_snapshot,after_snapshot,
               risk_level,status,requested_by,expected_version,expires_at)
            values ('%s','%s','CREATE',
                    '{"work_order_no":"WO-20260718-003","title":"duplicate","description":"d",
                      "project_id":"%s","space_path":"S","order_type":"REPAIR","priority":"HIGH",
                      "source":"MANUAL","due_at":"2026-07-19T02:00:00"}'::jsonb,
                    'null'::jsonb,'{"id":"%s"}'::jsonb,'MEDIUM','PENDING_CONFIRMATION','%s',0,
                    current_timestamp + interval '1 hour')
            """.formatted(proposal, TENANT, PROJECT, newId, USER));

        assertThatThrownBy(() -> service.execute(context(), reference(proposal), "duplicate-key"))
            .isInstanceOfSatisfying(WorkOrderVersionConflictException.class, conflict -> {
                assertThat(conflict.getFreshPreview().get("work_order_no").asText())
                    .isEqualTo("WO-20260718-003");
                assertThat(conflict.getFreshPreview().get("id").asText()).isEqualTo(TARGET.toString());
            });
    }

    @Test
    void databaseEnforcesOneOpenAssignmentAndImmutableEvents() throws Exception {
        executeAdmin("""
            insert into work_order_assignment
              (id,tenant_id,work_order_id,assignee_id,assigned_at,reason,created_by)
            values ('00000000-0000-0000-0000-000000009901','11111111-1111-1111-1111-111111111111',
                    '00000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000009001',
                    current_timestamp,'first','00000000-0000-0000-0000-000000009001')
            """);
        assertThatThrownBy(() -> executeAdmin("""
            insert into work_order_assignment
              (id,tenant_id,work_order_id,assignee_id,assigned_at,reason,created_by)
            values ('00000000-0000-0000-0000-000000009902','11111111-1111-1111-1111-111111111111',
                    '00000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000009001',
                    current_timestamp,'second','00000000-0000-0000-0000-000000009001')
            """)).isInstanceOf(java.sql.SQLException.class);

        UUID event = UUID.fromString("00000000-0000-0000-0000-000000009903");
        executeAdmin("""
            insert into work_order_event
              (id,tenant_id,work_order_id,event_type,command_type,before_snapshot,after_snapshot,
               actor_id,request_id,trace_id)
            values ('%s','%s','%s','TEST','UPDATE','{}'::jsonb,'{}'::jsonb,'%s','r','t')
            """.formatted(event, TENANT, TARGET, USER));
        assertThatThrownBy(() -> executeAdmin("update work_order_event set event_type='MUTATED' where id='" + event + "'"))
            .isInstanceOf(java.sql.SQLException.class);
        assertThatThrownBy(() -> executeAdmin("delete from work_order_event where id='" + event + "'"))
            .isInstanceOf(java.sql.SQLException.class);
    }

    @Test
    void sameProposalDifferentKeysStillExecuteExactlyOnce() throws Exception {
        UUID proposal = UUID.fromString("00000000-0000-0000-0000-000000009508");
        UUID target = UUID.fromString("00000000-0000-0000-0000-000000000006");
        seedUpdateProposal(proposal, target, "WO-20260718-006", 0, "PENDING_CONFIRMATION", "1 hour");
        TenantContext context = context();
        CountDownLatch start = new CountDownLatch(1);
        var pool = Executors.newFixedThreadPool(2);
        try {
            Future<WorkOrderExecutionResponse> first = pool.submit(() -> {
                start.await(); return service.execute(context, reference(proposal), "different-key-a");
            });
            Future<WorkOrderExecutionResponse> second = pool.submit(() -> {
                start.await(); return service.execute(context, reference(proposal), "different-key-b");
            });
            start.countDown();
            assertOneSuccessAndOneFailure(first, second, InvalidCommandException.class);
        } finally {
            pool.shutdownNow();
        }
        assertThat(queryCount("select count(*) from work_order_event where tenant_id='" + TENANT
            + "' and work_order_id='" + target + "'")).isEqualTo(1);
        assertThat(queryCount("select count(*) from idempotency_record where idempotency_key in "
            + "('different-key-a','different-key-b')")).isEqualTo(1);
    }

    @Test
    void sameKeyDifferentProposalsConflictsAndOnlyOneProposalExecutes() throws Exception {
        UUID firstProposal = UUID.fromString("00000000-0000-0000-0000-000000009509");
        UUID secondProposal = UUID.fromString("00000000-0000-0000-0000-000000009510");
        UUID firstTarget = UUID.fromString("00000000-0000-0000-0000-000000000006");
        UUID secondTarget = UUID.fromString("00000000-0000-0000-0000-000000000011");
        seedUpdateProposal(firstProposal, firstTarget, "WO-20260718-006", 0, "PENDING_CONFIRMATION", "1 hour");
        seedUpdateProposal(secondProposal, secondTarget, "WO-20260718-011", 0, "PENDING_CONFIRMATION", "1 hour");
        TenantContext context = context(Set.of(
            PROJECT, UUID.fromString("00000000-0000-0000-0000-000000010002")));
        CountDownLatch start = new CountDownLatch(1);
        var pool = Executors.newFixedThreadPool(2);
        try {
            Future<WorkOrderExecutionResponse> first = pool.submit(() -> {
                start.await(); return service.execute(context, reference(firstProposal), "shared-proposal-key");
            });
            Future<WorkOrderExecutionResponse> second = pool.submit(() -> {
                start.await(); return service.execute(context, reference(secondProposal), "shared-proposal-key");
            });
            start.countDown();
            assertOneSuccessAndOneFailure(first, second, IdempotencyConflictException.class);
        } finally {
            pool.shutdownNow();
        }
        assertThat(queryCount("select count(*) from work_order_event where tenant_id='" + TENANT
            + "' and work_order_id in ('" + firstTarget + "','" + secondTarget + "')")).isEqualTo(1);
        assertThat(queryCount("select count(*) from idempotency_record where idempotency_key='shared-proposal-key'"))
            .isEqualTo(1);
    }

    @Test
    void zeroRowOutboxWriteRollsBackPrimaryTransaction() throws Exception {
        UUID proposal = UUID.fromString("00000000-0000-0000-0000-000000009511");
        UUID target = UUID.fromString("00000000-0000-0000-0000-000000000012");
        seedUpdateProposal(proposal, target, "WO-20260718-012", 0, "PENDING_CONFIRMATION", "1 hour");
        executeAdmin("""
            create or replace function suppress_outbox_insert() returns trigger language plpgsql as $$
            begin return null; end $$;
            create trigger suppress_outbox_insert before insert on outbox_event
              for each row execute function suppress_outbox_insert();
            """);
        try {
            assertThatThrownBy(() -> service.execute(context(), reference(proposal), "zero-outbox-key"))
                .isInstanceOf(InvalidCommandException.class);
        } finally {
            executeAdmin("drop trigger if exists suppress_outbox_insert on outbox_event; "
                + "drop function if exists suppress_outbox_insert();");
        }
        assertThat(queryCount("select version from work_order where tenant_id='" + TENANT
            + "' and id='" + target + "'")).isZero();
        assertThat(queryCount("select count(*) from work_order_event where tenant_id='" + TENANT
            + "' and work_order_id='" + target + "'")).isZero();
        assertThat(queryText("select status from action_proposal where id='" + proposal + "'"))
            .isEqualTo("FAILED");
    }

    @Test
    void staleProposalConflictUsesCurrentDatabaseVersionPreview() throws Exception {
        UUID proposal = UUID.fromString("00000000-0000-0000-0000-000000009512");
        UUID target = UUID.fromString("00000000-0000-0000-0000-000000000013");
        executeAdmin("update work_order set version=2 where tenant_id='" + TENANT + "' and id='" + target + "'");
        seedUpdateProposal(proposal, target, "WO-20260718-013", 0, "PENDING_CONFIRMATION", "1 hour");

        assertThatThrownBy(() -> service.execute(context(), reference(proposal), "fresh-preview-key"))
            .isInstanceOfSatisfying(WorkOrderVersionConflictException.class, conflict ->
                assertThat(conflict.getFreshPreview().get("version").asLong()).isEqualTo(3L));
    }

    private TenantContext context() {
        return context(Set.of(PROJECT));
    }

    private TenantContext context(Set<UUID> projects) {
        return new TenantContext(TENANT, USER, "human", Set.of("DISPATCHER"), projects,
            Set.of("work-order:write"), "request", "trace");
    }

    private void assertOneSuccessAndOneFailure(Future<WorkOrderExecutionResponse> first,
                                               Future<WorkOrderExecutionResponse> second,
                                               Class<? extends Throwable> failureType) throws Exception {
        int successes = 0;
        int failures = 0;
        for (Future<WorkOrderExecutionResponse> future : java.util.List.of(first, second)) {
            try {
                assertThat(future.get()).isNotNull();
                successes++;
            } catch (ExecutionException exception) {
                assertThat(exception.getCause()).isInstanceOf(failureType);
                failures++;
            }
        }
        assertThat(successes).isEqualTo(1);
        assertThat(failures).isEqualTo(1);
    }

    private ActionProposalEntity reference(UUID proposalId) {
        return ActionProposalEntity.builder().id(proposalId).tenantId(TENANT).build();
    }

    private void seedUpdateProposal(UUID proposalId, UUID targetId, String workOrderNo,
                                    long version, String status, String expiry) throws Exception {
        executeAdmin("""
            insert into action_proposal
              (id,tenant_id,action_type,target_id,command_payload,before_snapshot,after_snapshot,
               risk_level,status,requested_by,expected_version,expires_at)
            values ('%s','%s','UPDATE','%s',
                    '{"target_work_order_no":"%s","title":"reliability"}'::jsonb,
                    '{}'::jsonb,'{}'::jsonb,'LOW','%s','%s',%d,current_timestamp + interval '%s')
            """.formatted(proposalId, TENANT, targetId, workOrderNo, status, USER, version, expiry));
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
