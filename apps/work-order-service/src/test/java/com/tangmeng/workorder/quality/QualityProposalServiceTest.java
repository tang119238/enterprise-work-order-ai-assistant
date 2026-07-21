package com.tangmeng.workorder.quality;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.tangmeng.workorder.api.ActionProposalResponse;
import com.tangmeng.workorder.command.ActionNotPermittedException;
import com.tangmeng.workorder.command.ActionProposalService;
import com.tangmeng.workorder.domain.ActionProposalEntity;
import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.mapper.ActionProposalMapper;
import com.tangmeng.workorder.mapper.ProjectMapper;
import com.tangmeng.workorder.mapper.WorkOrderMapper;
import com.tangmeng.workorder.security.TenantContext;
import com.tangmeng.workorder.tenant.TenantTransaction;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Set;
import java.util.UUID;
import java.util.function.Supplier;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class QualityProposalServiceTest {
    private static final UUID TENANT = UUID.fromString("11111111-1111-1111-1111-111111111111");
    private static final UUID USER = UUID.fromString("00000000-0000-0000-0000-000000009001");
    private static final UUID ORDER = UUID.fromString("22222222-2222-2222-2222-222222222222");
    private static final UUID RESULT = UUID.fromString("44444444-4444-4444-4444-444444444444");

    @Mock private ActionProposalMapper proposalMapper;
    @Mock private ProjectMapper projectMapper;
    @Mock private WorkOrderMapper workOrderMapper;
    @Mock private TenantTransaction transactions;

    private ObjectMapper mapper;
    private ActionProposalService service;

    @BeforeEach
    void setUp() {
        mapper = new ObjectMapper();
        service = new ActionProposalService(
            proposalMapper, projectMapper, workOrderMapper, transactions, mapper,
            Clock.fixed(Instant.parse("2026-07-20T08:00:00Z"), ZoneOffset.UTC));
        lenient().when(transactions.required(any(TenantContext.class), any())).thenAnswer(invocation ->
            ((Supplier<?>) invocation.getArgument(1)).get());
        lenient().when(proposalMapper.insert(any(ActionProposalEntity.class))).thenReturn(1);
    }

    @Test
    void failCreatesCriticalReworkPreviewLinkedToRootWithoutMutatingOrder() {
        WorkOrderEntity original = order();

        ActionProposalResponse response = service.createQualityProposal(
            context(Set.of("quality:callback")), original, callback("FAIL"));

        assertThat(response.actionType()).isEqualTo("CREATE_RECTIFICATION");
        assertThat(response.riskLevel()).isEqualTo("CRITICAL");
        assertThat(response.expectedVersion()).isEqualTo(7);
        assertThat(response.afterSnapshot().path("order_type").asText()).isEqualTo("REWORK");
        assertThat(response.afterSnapshot().path("root_work_order_id").asText())
            .isEqualTo(ORDER.toString());
        assertThat(response.afterSnapshot().path("status").asText())
            .isEqualTo("PENDING_DISPATCH");
        ArgumentCaptor<ActionProposalEntity> persisted =
            ArgumentCaptor.forClass(ActionProposalEntity.class);
        verify(proposalMapper).insert(persisted.capture());
        assertThat(persisted.getValue().getActionType()).isEqualTo("CREATE_RECTIFICATION");
        assertThat(persisted.getValue().getCommandPayload().path("quality_result_id").asText())
            .isEqualTo(RESULT.toString());
        assertThat(original.getStatus()).isEqualTo("COMPLETED");
        assertThat(original.getVersion()).isEqualTo(7);
    }

    @Test
    void passCreatesClosePreviewButStillOnlyPersistsProposal() {
        WorkOrderEntity original = order();

        ActionProposalResponse response = service.createQualityProposal(
            context(Set.of("quality:callback")), original, callback("PASS"));

        assertThat(response.actionType()).isEqualTo("CLOSE");
        assertThat(response.afterSnapshot().path("status").asText()).isEqualTo("CLOSED");
        assertThat(original.getStatus()).isEqualTo("COMPLETED");
        verify(proposalMapper).insert(any(ActionProposalEntity.class));
    }

    @Test
    void missingCallbackScopeCannotCreateProposalEvenWithAiRole() {
        assertThatThrownBy(() -> service.createQualityProposal(
            context(Set.of("work-order:read")), order(), callback("FAIL")))
            .isInstanceOf(ActionNotPermittedException.class);

        verify(proposalMapper, never()).insert(any(ActionProposalEntity.class));
    }

    private QualityResultCallback callback(String verdict) {
        return new QualityResultCallback(
            RESULT, UUID.fromString("33333333-3333-3333-3333-333333333333"), TENANT, ORDER,
            7, 1, verdict, 0.9,
            mapper.createObjectNode().put("id", ORDER.toString())
                .put("tenant_id", TENANT.toString()).put("version", 7)
                .put("status", "COMPLETED"),
            mapper.createObjectNode(), mapper.createArrayNode(), null
        );
    }

    private static WorkOrderEntity order() {
        return WorkOrderEntity.builder().id(ORDER).tenantId(TENANT).workOrderNo("WO-1")
            .title("Synthetic repair").description("Done")
            .projectId(UUID.fromString("00000000-0000-0000-0000-000000010001"))
            .projectName("North").spacePath("HQ/F2").orderType("REPAIR")
            .priority("HIGH").status("COMPLETED").source("API").version(7).build();
    }

    private static TenantContext context(Set<String> scopes) {
        return new TenantContext(
            TENANT, USER, "quality-service", Set.of("AI_SERVICE"), Set.of(), scopes,
            "quality-request", "quality-trace");
    }
}
