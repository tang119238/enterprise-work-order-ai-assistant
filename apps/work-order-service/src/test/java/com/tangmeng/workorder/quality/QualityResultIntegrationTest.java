package com.tangmeng.workorder.quality;

import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.tangmeng.workorder.api.ActionProposalResponse;
import com.tangmeng.workorder.command.ActionNotPermittedException;
import com.tangmeng.workorder.command.ActionProposalService;
import com.tangmeng.workorder.command.IdempotencyConflictException;
import com.tangmeng.workorder.command.WorkOrderCommandRepository;
import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.mapper.WorkOrderMapper;
import com.tangmeng.workorder.security.TenantContext;
import com.tangmeng.workorder.tenant.TenantTransaction;
import org.apache.ibatis.builder.MapperBuilderAssistant;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

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
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class QualityResultIntegrationTest {
    private static final UUID TENANT = UUID.fromString("11111111-1111-1111-1111-111111111111");
    private static final UUID OTHER_TENANT = UUID.fromString("99999999-9999-9999-9999-999999999999");
    private static final UUID USER = UUID.fromString("00000000-0000-0000-0000-000000009001");
    private static final UUID ORDER = UUID.fromString("22222222-2222-2222-2222-222222222222");
    private static final UUID RESULT = UUID.fromString("44444444-4444-4444-4444-444444444444");
    private static final UUID CASE = UUID.fromString("55555555-5555-5555-5555-555555555555");
    private static final UUID PROPOSAL = UUID.fromString("66666666-6666-6666-6666-666666666666");
    private static final LocalDateTime NOW = LocalDateTime.parse("2026-07-20T08:00:00");

    @Mock private RectificationRepository repository;
    @Mock private WorkOrderCommandRepository commandRepository;
    @Mock private WorkOrderMapper workOrderMapper;
    @Mock private ActionProposalService proposalService;
    @Mock private TenantTransaction transactions;

    private ObjectMapper objectMapper;
    private RectificationService service;

    @BeforeAll
    static void metadata() {
        MybatisConfiguration configuration = new MybatisConfiguration();
        TableInfoHelper.initTableInfo(
            new MapperBuilderAssistant(configuration, "quality-result-test"), WorkOrderEntity.class);
    }

    @BeforeEach
    void setUp() {
        objectMapper = new ObjectMapper().registerModule(new JavaTimeModule());
        service = new RectificationService(
            repository, commandRepository, workOrderMapper, proposalService, transactions,
            objectMapper, Clock.fixed(Instant.parse("2026-07-20T08:00:00Z"), ZoneOffset.UTC));
        lenient().when(transactions.required(any(TenantContext.class), any())).thenAnswer(invocation ->
            ((Supplier<?>) invocation.getArgument(1)).get());
        lenient().when(commandRepository.findIdempotency(eq(TENANT), any(), any()))
            .thenReturn(Optional.empty());
        lenient().when(commandRepository.reserveIdempotency(eq(TENANT), any(), any(), any(), eq(NOW)))
            .thenReturn(true);
        lenient().when(commandRepository.completeIdempotency(
            eq(TENANT), any(), any(), any(), any(), eq(200))).thenReturn(true);
        lenient().when(repository.lockWorkOrder(TENANT, ORDER)).thenReturn(true);
        lenient().when(workOrderMapper.selectOne(any())).thenReturn(order());
        lenient().when(repository.insertCase(any())).thenReturn(true);
        lenient().when(repository.insertReviewEvent(any(), any(), any(), any(), any(), any(), any(), any(), any()))
            .thenReturn(true);
    }

    @ParameterizedTest
    @CsvSource({"PASS,CLOSE", "FAIL,CREATE_RECTIFICATION", "UNCERTAIN,CREATE_RECTIFICATION"})
    void mapsQualityVerdictToOneHumanConfirmedProposal(String verdict, String action) {
        when(proposalService.createQualityProposal(eq(context()), any(), any()))
            .thenReturn(proposal(action));

        QualityResultCallbackResponse response = service.accept(
            context(), callback(verdict, TENANT, 7), RESULT.toString());

        assertThat(response.actionType()).isEqualTo(action);
        assertThat(response.status()).isEqualTo("PROPOSED");
        ArgumentCaptor<RectificationCaseEntity> captor =
            ArgumentCaptor.forClass(RectificationCaseEntity.class);
        verify(repository).insertCase(captor.capture());
        assertThat(captor.getValue().getCurrentQualityResultId()).isEqualTo(RESULT);
        assertThat(captor.getValue().getCurrentVerdict()).isEqualTo(verdict);
        assertThat(captor.getValue().getProposalId()).isEqualTo(PROPOSAL);
        verify(workOrderMapper).selectOne(any());
    }

    @Test
    void skipStoresOnlyIdempotentReceiptAndNeverCreatesStateChangingRecords() {
        QualityResultCallbackResponse response = service.accept(
            context(), callback("SKIP", TENANT, 7), RESULT.toString());

        assertThat(response.actionType()).isEqualTo("SKIP");
        assertThat(response.status()).isEqualTo("SKIPPED");
        verify(proposalService, never()).createQualityProposal(any(), any(), any());
        verify(repository, never()).insertCase(any());
        verify(repository, never()).insertReviewEvent(any(), any(), any(), any(), any(), any(), any(), any(), any());
    }

    @Test
    void repeatedResultWithNewDeliveryKeyReusesExistingCaseAndProposal() {
        RectificationCaseEntity existing = RectificationCaseEntity.builder()
            .id(CASE).tenantId(TENANT).originalWorkOrderId(ORDER)
            .currentQualityResultId(RESULT).currentVerdict("FAIL").proposalId(PROPOSAL)
            .inspectionRound(1).status("PROPOSED").build();
        when(repository.findByResult(TENANT, RESULT)).thenReturn(existing);

        QualityResultCallbackResponse response = service.accept(
            context(), callback("FAIL", TENANT, 7), "redelivery-key");

        assertThat(response.rectificationCaseId()).isEqualTo(CASE);
        assertThat(response.proposalId()).isEqualTo(PROPOSAL);
        verify(proposalService, never()).createQualityProposal(any(), any(), any());
        verify(repository, never()).insertCase(any());
    }

    @Test
    void sameRoundWithDifferentResultIsRejectedWithoutAnotherProposal() {
        when(repository.findByOriginalRound(TENANT, ORDER, 1)).thenReturn(
            RectificationCaseEntity.builder().id(CASE).tenantId(TENANT)
                .currentQualityResultId(UUID.randomUUID()).build());

        assertThatThrownBy(() -> service.accept(
            context(), callback("FAIL", TENANT, 7), RESULT.toString()))
            .isInstanceOf(IdempotencyConflictException.class);

        verify(proposalService, never()).createQualityProposal(any(), any(), any());
    }

    @Test
    void rejectsCrossTenantAndStaleVersionBeforeCreatingProposal() {
        assertThatThrownBy(() -> service.accept(
            context(), callback("FAIL", OTHER_TENANT, 7), RESULT.toString()))
            .isInstanceOf(ActionNotPermittedException.class);

        assertThatThrownBy(() -> service.accept(
            context(), callback("FAIL", TENANT, 6), "stale-key"))
            .isInstanceOf(com.tangmeng.workorder.command.InvalidCommandException.class);

        verify(proposalService, never()).createQualityProposal(any(), any(), any());
    }

    private QualityResultCallback callback(String verdict, UUID tenant, long version) {
        return new QualityResultCallback(
            RESULT, UUID.fromString("33333333-3333-3333-3333-333333333333"), tenant, ORDER,
            version, 1, verdict, 0.91,
            objectMapper.createObjectNode().put("id", ORDER.toString())
                .put("tenant_id", tenant.toString()).put("version", version)
                .put("status", "COMPLETED"),
            objectMapper.createObjectNode(), objectMapper.createArrayNode(), null
        );
    }

    private static WorkOrderEntity order() {
        return WorkOrderEntity.builder().id(ORDER).tenantId(TENANT).workOrderNo("WO-1")
            .title("Synthetic repair").description("Done")
            .projectId(UUID.fromString("00000000-0000-0000-0000-000000010001"))
            .projectName("North").spacePath("HQ/F2").orderType("REPAIR")
            .priority("HIGH").status("COMPLETED").source("API").version(7).build();
    }

    private ActionProposalResponse proposal(String action) {
        return new ActionProposalResponse(
            PROPOSAL, action, "WO-1", "HIGH", "PENDING_CONFIRMATION",
            objectMapper.createObjectNode(), objectMapper.createObjectNode(), 7,
            NOW.plusHours(24));
    }

    private static TenantContext context() {
        return new TenantContext(
            TENANT, USER, "quality-service", Set.of("AI_SERVICE"), Set.of(),
            Set.of("quality:callback"), "quality-request", "quality-trace");
    }
}
