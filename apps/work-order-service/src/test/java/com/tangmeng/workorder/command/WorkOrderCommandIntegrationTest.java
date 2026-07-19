package com.tangmeng.workorder.command;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.tangmeng.workorder.api.WorkOrderExecutionResponse;
import com.tangmeng.workorder.domain.ActionProposalEntity;
import com.tangmeng.workorder.domain.ProjectEntity;
import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.domain.WorkOrderEventEntity;
import com.tangmeng.workorder.security.TenantContext;
import com.tangmeng.workorder.tenant.TenantAccessService;
import com.tangmeng.workorder.tenant.TenantTransaction;
import com.tangmeng.workorder.service.WorkOrderNotFoundException;
import com.tangmeng.workorder.service.InvalidStateTransitionException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.InOrder;
import org.mockito.ArgumentCaptor;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;

import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.function.Supplier;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import java.util.stream.Stream;

class WorkOrderCommandIntegrationTest {

    private static final UUID TENANT = UUID.fromString("11111111-1111-1111-1111-111111111111");
    private static final UUID USER = UUID.fromString("00000000-0000-0000-0000-000000009001");
    private static final UUID PROJECT = UUID.fromString("00000000-0000-0000-0000-000000010001");
    private static final UUID TARGET = UUID.fromString("00000000-0000-0000-0000-000000000001");
    private static final UUID PROPOSAL = UUID.fromString("00000000-0000-0000-0000-000000009101");
    private static final LocalDateTime NOW = LocalDateTime.parse("2026-07-18T02:00:00");
    private static final Clock CLOCK = Clock.fixed(Instant.parse("2026-07-18T02:00:00Z"), ZoneOffset.UTC);

    private final WorkOrderCommandRepository repository = mock(WorkOrderCommandRepository.class);
    private final TenantTransaction transactions = mock(TenantTransaction.class);
    private final TenantAccessService access = mock(TenantAccessService.class);
    private final ObjectMapper mapper = new ObjectMapper();
    private WorkOrderCommandService service;

    @BeforeEach
    void setUp() {
        service = new WorkOrderCommandService(repository, transactions, access, mapper, CLOCK);
        when(transactions.required(any(TenantContext.class), any())).thenAnswer(call ->
            ((Supplier<?>) call.getArgument(1)).get());
        when(access.loadCurrentUserId(TENANT, "human")).thenReturn(USER);
        when(access.loadCurrentRoles(TENANT, "human")).thenReturn(Set.of("DISPATCHER"));
        when(access.loadCurrentProjects(TENANT, "human")).thenReturn(Set.of(PROJECT));
        when(repository.findIdempotency(TENANT, WorkOrderCommandService.CONFIRM_OPERATION, "key"))
            .thenReturn(Optional.empty());
        when(repository.reserveIdempotency(eq(TENANT), eq(WorkOrderCommandService.CONFIRM_OPERATION),
            any(), any(), eq(NOW))).thenReturn(true);
        when(repository.claimProposal(TENANT, PROPOSAL, USER, NOW)).thenReturn(true);
        when(repository.updateWorkOrder(any(), eq(7L))).thenReturn(true);
        when(repository.insertEvent(any())).thenReturn(1);
        when(repository.insertOutbox(any(), any(), any(), any(), any())).thenReturn(1);
        when(repository.insertAssignment(any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(repository.completeIdempotency(any(), any(), any(), any(), any(), any(Integer.class))).thenReturn(true);
        when(repository.markProposalExecuted(eq(TENANT), eq(PROPOSAL), any(), eq(NOW))).thenReturn(true);
    }

    @Test
    void executesUpdateInTheExactTransactionOrderAndIncrementsOneVersion() {
        ActionProposalEntity proposal = proposal("UPDATE", payload().put("title", "new title"));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(repository.findWorkOrder(TENANT, TARGET, Set.of(PROJECT))).thenReturn(order("PROCESSING"));

        WorkOrderExecutionResponse response = service.execute(context("DISPATCHER"), proposal, "key");

        assertThat(response.version()).isEqualTo(8L);
        assertThat(response.status()).isEqualTo("PROCESSING");
        InOrder ordered = inOrder(repository);
        ordered.verify(repository).findIdempotency(TENANT, WorkOrderCommandService.CONFIRM_OPERATION, "key");
        ordered.verify(repository).reserveIdempotency(eq(TENANT), eq(WorkOrderCommandService.CONFIRM_OPERATION),
            eq("key"), any(), eq(NOW));
        ordered.verify(repository).findProposal(TENANT, PROPOSAL);
        ordered.verify(repository).claimProposal(TENANT, PROPOSAL, USER, NOW);
        ordered.verify(repository).updateWorkOrder(any(), eq(7L));
        ordered.verify(repository).insertEvent(any(WorkOrderEventEntity.class));
        ordered.verify(repository).insertOutbox(eq(TENANT), eq(TARGET), eq("WORK_ORDER_UPDATED"), any(), eq(NOW));
        ordered.verify(repository).completeIdempotency(eq(TENANT), eq(WorkOrderCommandService.CONFIRM_OPERATION),
            eq("key"), any(), any(), eq(200));
        ordered.verify(repository).markProposalExecuted(eq(TENANT), eq(PROPOSAL), any(), eq(NOW));
    }

    @Test
    void replayReturnsStoredResponseWithoutClaimingOrWritingAgain() {
        ActionProposalEntity proposal = proposal("UPDATE", payload().put("title", "new title"));
        proposal.setStatus("EXECUTED");
        WorkOrderExecutionResponse stored = new WorkOrderExecutionResponse(
            PROPOSAL, TARGET, "WO-1", "UPDATE", "PROCESSING", 8L);
        String hash = WorkOrderCommandService.requestHash(mapper, PROPOSAL, proposal.getCommandPayload());
        when(repository.findIdempotency(TENANT, WorkOrderCommandService.CONFIRM_OPERATION, "key"))
            .thenReturn(Optional.of(new WorkOrderCommandRepository.StoredIdempotency(
                hash, mapper.valueToTree(stored), 200)));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(repository.findWorkOrder(TENANT, TARGET, Set.of(PROJECT))).thenReturn(order("PROCESSING"));

        assertThat(service.execute(context("DISPATCHER"), proposal, "key")).isEqualTo(stored);
        verify(repository, never()).claimProposal(any(), any(), any(), any());
        verify(repository, never()).insertEvent(any());
    }

    @Test
    void executedReplayRemainsAvailableAfterThePreExecutionExpiryTime() {
        ActionProposalEntity proposal = proposal("UPDATE", payload().put("title", "new title"));
        proposal.setStatus("EXECUTED");
        proposal.setExpiresAt(NOW.minusMinutes(1));
        WorkOrderExecutionResponse stored = new WorkOrderExecutionResponse(
            PROPOSAL, TARGET, "WO-1", "UPDATE", "PROCESSING", 8L);
        String hash = WorkOrderCommandService.requestHash(mapper, PROPOSAL, proposal.getCommandPayload());
        when(repository.findIdempotency(TENANT, WorkOrderCommandService.CONFIRM_OPERATION, "key"))
            .thenReturn(Optional.of(new WorkOrderCommandRepository.StoredIdempotency(
                hash, mapper.valueToTree(stored), 200)));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(repository.findWorkOrder(TENANT, TARGET, Set.of(PROJECT))).thenReturn(order("PROCESSING"));

        assertThat(service.execute(context("DISPATCHER"), proposal, "key")).isEqualTo(stored);
        verify(repository, never()).markProposalExpired(any(), any(), any());
    }

    @Test
    void sameKeyWithDifferentProposalPayloadIsAStableConflict() {
        ActionProposalEntity proposal = proposal("UPDATE", payload().put("title", "different"));
        when(repository.findIdempotency(TENANT, WorkOrderCommandService.CONFIRM_OPERATION, "key"))
            .thenReturn(Optional.of(new WorkOrderCommandRepository.StoredIdempotency(
                "other-hash", mapper.createObjectNode(), 200)));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(repository.findWorkOrder(TENANT, TARGET, Set.of(PROJECT))).thenReturn(order("PROCESSING"));

        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), proposal, "key"))
            .isInstanceOf(IdempotencyConflictException.class);
        verify(repository, never()).claimProposal(any(), any(), any(), any());
    }

    @Test
    void mismatchedExistingKeyDoesNotRevealItAfterCurrentRoleRevocation() {
        ActionProposalEntity proposal = proposal("UPDATE", payload().put("title", "different"));
        when(repository.findIdempotency(TENANT, WorkOrderCommandService.CONFIRM_OPERATION, "key"))
            .thenReturn(Optional.of(new WorkOrderCommandRepository.StoredIdempotency(
                "other-hash", mapper.createObjectNode(), 200)));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(access.loadCurrentRoles(TENANT, "human")).thenReturn(Set.of());

        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), proposal, "key"))
            .isInstanceOf(ActionNotPermittedException.class);
        verifyNoProposalMutation();
    }

    @Test
    void mismatchedExistingKeyDoesNotRevealItToAiMultiRoleCaller() {
        ActionProposalEntity proposal = proposal("UPDATE", payload().put("title", "different"));
        when(repository.findIdempotency(TENANT, WorkOrderCommandService.CONFIRM_OPERATION, "key"))
            .thenReturn(Optional.of(new WorkOrderCommandRepository.StoredIdempotency(
                "other-hash", mapper.createObjectNode(), 200)));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(access.loadCurrentRoles(TENANT, "human")).thenReturn(Set.of("DISPATCHER", "AI_SERVICE"));
        TenantContext aiMultiRole = new TenantContext(TENANT, USER, "human",
            Set.of("DISPATCHER", "AI_SERVICE"), Set.of(PROJECT),
            Set.of("work-order:write"), "request", "trace");

        assertThatThrownBy(() -> service.execute(aiMultiRole, proposal, "key"))
            .isInstanceOf(ActionNotPermittedException.class);
        verifyNoProposalMutation();
    }

    @Test
    void mismatchedExistingKeyDoesNotRevealItAcrossProposalTenantBoundary() {
        ActionProposalEntity proposal = proposal("UPDATE", payload().put("title", "different"));
        when(repository.findIdempotency(TENANT, WorkOrderCommandService.CONFIRM_OPERATION, "key"))
            .thenReturn(Optional.of(new WorkOrderCommandRepository.StoredIdempotency(
                "other-hash", mapper.createObjectNode(), 200)));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(null);

        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), proposal, "key"))
            .isInstanceOf(WorkOrderNotFoundException.class);
        verifyNoProposalMutation();
    }

    @Test
    void mismatchedExistingKeyDoesNotRevealItOutsideCurrentProjectScope() {
        ActionProposalEntity proposal = proposal("UPDATE", payload().put("title", "different"));
        when(repository.findIdempotency(TENANT, WorkOrderCommandService.CONFIRM_OPERATION, "key"))
            .thenReturn(Optional.of(new WorkOrderCommandRepository.StoredIdempotency(
                "other-hash", mapper.createObjectNode(), 200)));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(repository.findWorkOrder(TENANT, TARGET, Set.of(PROJECT))).thenReturn(null);

        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), proposal, "key"))
            .isInstanceOf(WorkOrderNotFoundException.class);
        verifyNoProposalMutation();
    }

    @Test
    void rechecksCurrentPermissionAndAlwaysDeniesAiService() {
        ActionProposalEntity proposal = proposal("UPDATE", payload().put("title", "new title"));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(access.loadCurrentRoles(TENANT, "human")).thenReturn(Set.of());

        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), proposal, "key"))
            .isInstanceOf(ActionNotPermittedException.class);
        assertThatThrownBy(() -> service.execute(context("AI_SERVICE"), proposal, "key"))
            .isInstanceOf(ActionNotPermittedException.class);
        verify(repository, never()).updateWorkOrder(any(), any(Long.class));
        verify(repository, never()).markProposalFailed(any(), any(), any(), any(), any());
    }

    @Test
    void replayRechecksCurrentAuthorityBeforeReturningStoredResponse() {
        ActionProposalEntity proposal = proposal("UPDATE", payload().put("title", "new title"));
        proposal.setStatus("EXECUTED");
        WorkOrderExecutionResponse stored = new WorkOrderExecutionResponse(
            PROPOSAL, TARGET, "WO-1", "UPDATE", "PROCESSING", 8L);
        String hash = WorkOrderCommandService.requestHash(mapper, PROPOSAL, proposal.getCommandPayload());
        when(repository.findIdempotency(TENANT, WorkOrderCommandService.CONFIRM_OPERATION, "key"))
            .thenReturn(Optional.of(new WorkOrderCommandRepository.StoredIdempotency(
                hash, mapper.valueToTree(stored), 200)));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(access.loadCurrentRoles(TENANT, "human")).thenReturn(Set.of());

        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), proposal, "key"))
            .isInstanceOf(ActionNotPermittedException.class);
        verify(repository, never()).markProposalFailed(any(), any(), any(), any(), any());
    }

    @Test
    void incompleteSameHashIdempotencyRecordIsRejectedWithoutDeserializingNull() {
        ActionProposalEntity proposal = proposal("UPDATE", payload().put("title", "new title"));
        proposal.setStatus("EXECUTED");
        String hash = WorkOrderCommandService.requestHash(mapper, PROPOSAL, proposal.getCommandPayload());
        when(repository.findIdempotency(TENANT, WorkOrderCommandService.CONFIRM_OPERATION, "key"))
            .thenReturn(Optional.of(new WorkOrderCommandRepository.StoredIdempotency(hash, null, 0)));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(repository.findWorkOrder(TENANT, TARGET, Set.of(PROJECT))).thenReturn(order("PROCESSING"));

        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), proposal, "key"))
            .isInstanceOf(InvalidCommandException.class);
        verify(repository, never()).claimProposal(any(), any(), any(), any());
        verify(repository, never()).markProposalFailed(any(), any(), any(), any(), any());
    }

    @Test
    void staleVersionReturnsFreshPreviewAndNeverOverwrites() {
        ActionProposalEntity proposal = proposal("UPDATE", payload().put("title", "new title"));
        WorkOrderEntity current = order("PROCESSING");
        current.setVersion(8L);
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(repository.findWorkOrder(TENANT, TARGET, Set.of(PROJECT))).thenReturn(current);

        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), proposal, "key"))
            .isInstanceOfSatisfying(WorkOrderVersionConflictException.class,
                conflict -> assertThat(conflict.getFreshPreview().get("version").asLong()).isEqualTo(9L));
        verify(repository, never()).updateWorkOrder(any(), any(Long.class));
        verify(repository).markProposalFailed(TENANT, PROPOSAL, USER, "WORK_ORDER_VERSION_CONFLICT", NOW);
    }

    @Test
    void expiredProposalUsesGoneAndDoesNotClaim() {
        ActionProposalEntity expired = proposal("UPDATE", payload().put("title", "new title"));
        expired.setExpiresAt(NOW.minusSeconds(1));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(expired);

        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), expired, "key"))
            .isInstanceOf(ActionProposalExpiredException.class);
        verify(repository, never()).claimProposal(any(), any(), any(), any());
    }

    @Test
    void alreadyExpiredProposalRemainsStableGoneOnRepeatedAttempts() {
        ActionProposalEntity expired = proposal("UPDATE", payload().put("title", "new title"));
        expired.setStatus("EXPIRED");
        expired.setExpiresAt(NOW.minusMinutes(1));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(expired);

        for (String key : Set.of("expired-1", "expired-2")) {
            when(repository.findIdempotency(TENANT, WorkOrderCommandService.CONFIRM_OPERATION, key))
                .thenReturn(Optional.empty());
            assertThatThrownBy(() -> service.execute(context("DISPATCHER"), expired, key))
                .isInstanceOf(ActionProposalExpiredException.class);
        }
        verify(repository, never()).markProposalFailed(any(), any(), any(), any(), any());
    }

    @Test
    void zeroRowUpdateReloadsCurrentDatabaseRowForFreshConflictPreview() {
        ActionProposalEntity proposal = proposal("UPDATE", payload().put("title", "new title"));
        WorkOrderEntity stale = order("PROCESSING");
        WorkOrderEntity fresh = order("PROCESSING");
        fresh.setVersion(8L);
        fresh.setTitle("concurrent title");
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(repository.findWorkOrder(TENANT, TARGET, Set.of(PROJECT))).thenReturn(stale, stale, fresh);
        when(repository.updateWorkOrder(any(), eq(7L))).thenReturn(false);

        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), proposal, "key"))
            .isInstanceOfSatisfying(WorkOrderVersionConflictException.class, conflict -> {
                assertThat(conflict.getFreshPreview().get("version").asLong()).isEqualTo(9L);
                assertThat(conflict.getFreshPreview().get("title").asText()).isEqualTo("new title");
            });
        verify(repository, org.mockito.Mockito.atLeast(2))
            .findWorkOrder(TENANT, TARGET, Set.of(PROJECT));
    }

    static Stream<Arguments> targetActions() {
        return Stream.of(
            Arguments.of("ASSIGN", "DISPATCHER", "PENDING_DISPATCH", null, null, "PENDING_ACCEPTANCE",
                "{\"target_work_order_no\":\"WO-1\",\"assignee_id\":\"00000000-0000-0000-0000-000000009001\",\"assignee_name\":\"Human\",\"reason\":\"dispatch\"}"),
            Arguments.of("UPDATE", "DISPATCHER", "PROCESSING", null, null, "PROCESSING",
                "{\"target_work_order_no\":\"WO-1\",\"title\":\"changed\"}"),
            Arguments.of("ACCEPT", "OPERATOR", "PENDING_ACCEPTANCE", USER, null, "PENDING_ACCEPTANCE",
                "{\"target_work_order_no\":\"WO-1\"}"),
            Arguments.of("START", "OPERATOR", "PENDING_ACCEPTANCE", USER, NOW.minusMinutes(5), "PROCESSING",
                "{\"target_work_order_no\":\"WO-1\"}"),
            Arguments.of("COMPLETE", "OPERATOR", "PROCESSING", USER, NOW.minusMinutes(5), "COMPLETED",
                "{\"target_work_order_no\":\"WO-1\"}"),
            Arguments.of("CLOSE", "QUALITY_REVIEWER", "COMPLETED", null, NOW.minusMinutes(5), "CLOSED",
                "{\"target_work_order_no\":\"WO-1\"}"),
            Arguments.of("CANCEL", "DISPATCHER", "PROCESSING", null, NOW.minusMinutes(5), "CANCELLED",
                "{\"target_work_order_no\":\"WO-1\",\"reason\":\"customer request\"}")
        );
    }

    @ParameterizedTest
    @MethodSource("targetActions")
    void executesEveryTargetActionWithExactlyOneVersionIncrement(
        String action, String role, String initialStatus, UUID assignee,
        LocalDateTime acceptedAt, String expectedStatus, String json
    ) throws Exception {
        when(access.loadCurrentRoles(TENANT, "human")).thenReturn(Set.of(role));
        ActionProposalEntity proposal = proposal(action, (ObjectNode) mapper.readTree(json));
        WorkOrderEntity order = order(initialStatus);
        order.setAssigneeId(assignee);
        order.setAcceptedAt(acceptedAt);
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(repository.findWorkOrder(TENANT, TARGET, Set.of(PROJECT))).thenReturn(order);

        WorkOrderExecutionResponse response = service.execute(context(role), proposal, "key");

        assertThat(response.status()).isEqualTo(expectedStatus);
        assertThat(response.version()).isEqualTo(8L);
        ArgumentCaptor<WorkOrderEntity> changed = ArgumentCaptor.forClass(WorkOrderEntity.class);
        verify(repository).updateWorkOrder(changed.capture(), eq(7L));
        assertThat(changed.getValue().getVersion()).isEqualTo(8L);
        if ("ASSIGN".equals(action)) {
            verify(repository).insertAssignment(TENANT, TARGET, USER, "dispatch", USER, NOW);
        }
        if ("COMPLETE".equals(action)) {
            ArgumentCaptor<JsonNode> outboxPayload = ArgumentCaptor.forClass(JsonNode.class);
            verify(repository).insertOutbox(
                eq(TENANT), eq(TARGET), eq("WORK_ORDER_COMPLETED"),
                outboxPayload.capture(), eq(NOW)
            );
            assertThat(outboxPayload.getValue().path("attachments_summary").isArray()).isTrue();
            assertThat(outboxPayload.getValue().path("attachments_summary").isEmpty()).isTrue();
            assertThat(outboxPayload.getValue().path("inspection_round").asInt()).isEqualTo(1);
            assertThat(outboxPayload.getValue().toString()).doesNotContain(
                "attachment_url", "database_url", "password", "credential"
            );
        }
    }

    @Test
    void executesCreateWithTenantSafeInitialVersionAndAuditRows() throws Exception {
        ObjectNode command = (ObjectNode) mapper.readTree("""
            {"work_order_no":"WO-NEW","title":"new","description":"d",
             "project_id":"00000000-0000-0000-0000-000000010001","project_name":"P",
             "space_path":"S","order_type":"REPAIR","priority":"HIGH","source":"MANUAL",
             "due_at":"2026-07-19T02:00:00"}
            """);
        ActionProposalEntity proposal = proposal("CREATE", command);
        proposal.setTargetId(null);
        proposal.setExpectedVersion(0L);
        proposal.setAfterSnapshot(mapper.createObjectNode().put("id", TARGET.toString()));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(repository.findProject(TENANT, PROJECT, Set.of(PROJECT))).thenReturn(
            ProjectEntity.builder().id(PROJECT).tenantId(TENANT).name("P").status("ACTIVE").build());
        when(repository.insertWorkOrder(any())).thenReturn(WorkOrderCommandRepository.InsertWorkOrderResult.INSERTED);

        WorkOrderExecutionResponse response = service.execute(context("DISPATCHER"), proposal, "key");

        assertThat(response.version()).isZero();
        assertThat(response.status()).isEqualTo("PENDING_DISPATCH");
        verify(repository).insertWorkOrder(any());
        verify(repository).insertEvent(any());
        verify(repository).insertOutbox(eq(TENANT), eq(TARGET), eq("WORK_ORDER_CREATED"), any(), eq(NOW));
    }

    @Test
    void duplicateCreateReturnsConflictFromFreshDatabaseRowButOtherIntegrityFailuresAreInvalid() throws Exception {
        ObjectNode command = (ObjectNode) mapper.readTree("""
            {"work_order_no":"WO-DUP","title":"new","description":"d",
             "project_id":"00000000-0000-0000-0000-000000010001","project_name":"P",
             "space_path":"S","order_type":"REPAIR","priority":"HIGH","source":"MANUAL",
             "due_at":"2026-07-19T02:00:00"}
            """);
        ActionProposalEntity proposal = proposal("CREATE", command);
        proposal.setTargetId(null);
        proposal.setExpectedVersion(0L);
        proposal.setAfterSnapshot(mapper.createObjectNode().put("id", TARGET.toString()));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(repository.findProject(TENANT, PROJECT, Set.of(PROJECT))).thenReturn(
            ProjectEntity.builder().id(PROJECT).tenantId(TENANT).name("P").status("ACTIVE").build());
        when(repository.insertWorkOrder(any())).thenReturn(WorkOrderCommandRepository.InsertWorkOrderResult.DUPLICATE);
        WorkOrderEntity duplicate = order("PROCESSING");
        duplicate.setVersion(4L);
        duplicate.setWorkOrderNo("WO-DUP");
        when(repository.findWorkOrderByIdentity(TENANT, TARGET, "WO-DUP", Set.of(PROJECT)))
            .thenReturn(duplicate);

        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), proposal, "key"))
            .isInstanceOfSatisfying(WorkOrderVersionConflictException.class, conflict ->
                assertThat(conflict.getFreshPreview().get("version").asLong()).isEqualTo(4L));

        when(repository.insertWorkOrder(any())).thenReturn(WorkOrderCommandRepository.InsertWorkOrderResult.INVALID);
        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), proposal, "other-key"))
            .isInstanceOf(InvalidCommandException.class);
    }

    @Test
    void uniqueReservationRaceRereadsCompletedSameHashResponse() {
        ActionProposalEntity proposal = proposal("UPDATE", payload().put("title", "new title"));
        proposal.setStatus("EXECUTED");
        WorkOrderExecutionResponse stored = new WorkOrderExecutionResponse(
            PROPOSAL, TARGET, "WO-1", "UPDATE", "PROCESSING", 8L);
        String hash = WorkOrderCommandService.requestHash(mapper, PROPOSAL, proposal.getCommandPayload());
        when(repository.findIdempotency(TENANT, WorkOrderCommandService.CONFIRM_OPERATION, "race-key"))
            .thenReturn(Optional.empty(), Optional.of(new WorkOrderCommandRepository.StoredIdempotency(
                hash, mapper.valueToTree(stored), 200)));
        when(repository.reserveIdempotency(TENANT, WorkOrderCommandService.CONFIRM_OPERATION,
            "race-key", hash, NOW)).thenReturn(false);
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(repository.findWorkOrder(TENANT, TARGET, Set.of(PROJECT))).thenReturn(order("PROCESSING"));

        assertThat(service.execute(context("DISPATCHER"), proposal, "race-key")).isEqualTo(stored);
        verify(repository, never()).claimProposal(any(), any(), any(), any());
    }

    @Test
    void everyReliabilityWriteChecksItsAffectedRowCount() {
        ActionProposalEntity proposal = proposal("UPDATE", payload().put("title", "new title"));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(repository.findWorkOrder(TENANT, TARGET, Set.of(PROJECT))).thenReturn(order("PROCESSING"));

        when(repository.insertEvent(any())).thenReturn(0);
        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), proposal, "event-key"))
            .isInstanceOf(InvalidCommandException.class);

        when(repository.insertEvent(any())).thenReturn(1);
        when(repository.insertOutbox(any(), any(), any(), any(), any())).thenReturn(0);
        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), proposal, "outbox-key"))
            .isInstanceOf(InvalidCommandException.class);

        when(repository.insertOutbox(any(), any(), any(), any(), any())).thenReturn(1);
        when(repository.completeIdempotency(any(), any(), any(), any(), any(), any(Integer.class))).thenReturn(false);
        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), proposal, "idem-key"))
            .isInstanceOf(IllegalStateException.class).hasMessage("Idempotency completion failed");
    }

    @Test
    void assignmentChangeRejectsUnexpectedCloseAndInsertCounts() throws Exception {
        ActionProposalEntity proposal = proposal("ASSIGN", (ObjectNode) mapper.readTree("""
            {"target_work_order_no":"WO-1","assignee_id":"00000000-0000-0000-0000-000000009001",
             "assignee_name":"Human","reason":"dispatch"}
            """));
        WorkOrderEntity current = order("PENDING_DISPATCH");
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(repository.findWorkOrder(TENANT, TARGET, Set.of(PROJECT))).thenReturn(current);
        when(repository.closeOpenAssignment(TENANT, TARGET, NOW)).thenReturn(2);

        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), proposal, "close-key"))
            .isInstanceOf(InvalidCommandException.class);

        when(repository.closeOpenAssignment(TENANT, TARGET, NOW)).thenReturn(0);
        when(repository.insertAssignment(TENANT, TARGET, USER, "dispatch", USER, NOW)).thenReturn(0);
        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), proposal, "assignment-key"))
            .isInstanceOf(InvalidCommandException.class);
    }

    @Test
    void eventFailureStopsOutboxIdempotencyAndCompletionThenUsesRecoveryTransaction() {
        ActionProposalEntity proposal = proposal("UPDATE", payload().put("title", "new title"));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(repository.findWorkOrder(TENANT, TARGET, Set.of(PROJECT))).thenReturn(order("PROCESSING"));
        when(repository.insertEvent(any())).thenThrow(new IllegalStateException("forced event failure"));

        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), proposal, "key"))
            .isInstanceOf(IllegalStateException.class).hasMessage("forced event failure");
        verify(repository, never()).insertOutbox(any(), any(), any(), any(), any());
        verify(repository, never()).completeIdempotency(any(), any(), any(), any(), any(), any(Integer.class));
        verify(repository, never()).markProposalExecuted(any(), any(), any(), any());
        verify(repository).markProposalFailed(TENANT, PROPOSAL, USER, "INTERNAL_ERROR", NOW);
    }

    @Test
    void crossProjectAndCrossTenantTargetsRemainHiddenAndAreNeverClaimed() {
        ActionProposalEntity proposal = proposal("UPDATE", payload().put("title", "hidden"));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(access.loadCurrentProjects(TENANT, "human")).thenReturn(Set.of());

        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), proposal, "key"))
            .isInstanceOf(WorkOrderNotFoundException.class);

        ActionProposalEntity otherTenantReference = ActionProposalEntity.builder()
            .id(UUID.randomUUID()).tenantId(UUID.randomUUID()).build();
        assertThatThrownBy(() -> service.execute(context("DISPATCHER"), otherTenantReference, "other-key"))
            .isInstanceOf(WorkOrderNotFoundException.class);
        verify(repository, never()).claimProposal(any(), any(), any(), any());
    }

    @Test
    void invalidCurrentStateRollsBackClaimAndUsesStableTransitionError() {
        ActionProposalEntity proposal = proposal("START", payload());
        when(access.loadCurrentRoles(TENANT, "human")).thenReturn(Set.of("OPERATOR"));
        WorkOrderEntity current = order("PROCESSING");
        current.setAssigneeId(USER);
        current.setAcceptedAt(NOW.minusMinutes(5));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(repository.findWorkOrder(TENANT, TARGET, Set.of(PROJECT))).thenReturn(current);

        assertThatThrownBy(() -> service.execute(context("OPERATOR"), proposal, "key"))
            .isInstanceOf(InvalidStateTransitionException.class)
            .hasMessage("INVALID_STATE_TRANSITION");
        verify(repository, never()).insertEvent(any());
        verify(repository).markProposalFailed(TENANT, PROPOSAL, USER, "INVALID_STATE_TRANSITION", NOW);
    }

    @Test
    void humanRejectionIsAtomicAndNeverMutatesTheWorkOrder() {
        ActionProposalEntity proposal = proposal("UPDATE", payload().put("title", "rejected"));
        when(repository.findProposal(TENANT, PROPOSAL)).thenReturn(proposal);
        when(repository.findWorkOrder(TENANT, TARGET, Set.of(PROJECT))).thenReturn(order("PROCESSING"));
        when(repository.rejectProposal(TENANT, PROPOSAL, USER, NOW)).thenReturn(true);

        assertThat(service.reject(context("DISPATCHER"), PROPOSAL)).isTrue();
        verify(repository).rejectProposal(TENANT, PROPOSAL, USER, NOW);
        verify(repository, never()).updateWorkOrder(any(), any(Long.class));
        verify(repository, never()).insertEvent(any());
        verify(repository, never()).insertOutbox(any(), any(), any(), any(), any());
    }

    @Test
    void canonicalConfirmationHashIncludesProposalIdAndIgnoresJsonPropertyOrder() throws Exception {
        UUID otherProposal = UUID.fromString("00000000-0000-0000-0000-000000009102");
        ObjectNode first = (ObjectNode) mapper.readTree("{\"a\":1,\"b\":2}");
        ObjectNode reordered = (ObjectNode) mapper.readTree("{\"b\":2,\"a\":1}");

        assertThat(WorkOrderCommandService.requestHash(mapper, PROPOSAL, first))
            .isEqualTo(WorkOrderCommandService.requestHash(mapper, PROPOSAL, reordered))
            .isNotEqualTo(WorkOrderCommandService.requestHash(mapper, otherProposal, first));
    }

    private ActionProposalEntity proposal(String action, ObjectNode payload) {
        return ActionProposalEntity.builder().id(PROPOSAL).tenantId(TENANT).actionType(action)
            .targetId(TARGET).commandPayload(payload).status("PENDING_CONFIRMATION")
            .expectedVersion(7L).expiresAt(NOW.plusMinutes(10)).build();
    }

    private ObjectNode payload() {
        return mapper.createObjectNode().put("target_work_order_no", "WO-1");
    }

    private WorkOrderEntity order(String status) {
        return WorkOrderEntity.builder().id(TARGET).tenantId(TENANT).projectId(PROJECT)
            .workOrderNo("WO-1").title("old").description("d").projectName("P")
            .spacePath("S").orderType("REPAIR").priority("HIGH").status(status)
            .source("MANUAL").version(7L).createdAt(NOW.minusDays(1)).dueAt(NOW.plusDays(1)).build();
    }

    private void verifyNoProposalMutation() {
        verify(repository, never()).claimProposal(any(), any(), any(), any());
        verify(repository, never()).markProposalFailed(any(), any(), any(), any(), any());
        verify(repository, never()).markProposalExecuted(any(), any(), any(), any());
    }

    private TenantContext context(String role) {
        return new TenantContext(TENANT, USER, "human", Set.of(role), Set.of(PROJECT),
            Set.of("work-order:write"), "request", "trace");
    }
}
