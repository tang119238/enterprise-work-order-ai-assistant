package com.tangmeng.workorder.command;

import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.conditions.AbstractWrapper;
import com.baomidou.mybatisplus.core.conditions.Wrapper;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.tangmeng.workorder.api.ActionProposalRequest;
import com.tangmeng.workorder.api.ActionProposalResponse;
import com.tangmeng.workorder.command.model.CreateProposalCommand;
import com.tangmeng.workorder.domain.ActionProposalEntity;
import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.mapper.ActionProposalMapper;
import com.tangmeng.workorder.mapper.WorkOrderMapper;
import com.tangmeng.workorder.security.TenantContext;
import com.tangmeng.workorder.service.InvalidStateTransitionException;
import com.tangmeng.workorder.service.WorkOrderNotFoundException;
import com.tangmeng.workorder.tenant.TenantTransaction;
import org.apache.ibatis.builder.MapperBuilderAssistant;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.function.Supplier;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.mockito.Mockito.lenient;

@ExtendWith(MockitoExtension.class)
@SuppressWarnings({"unchecked", "rawtypes"})
class ActionProposalServiceTest {

    private static final UUID TENANT = UUID.fromString("11111111-1111-1111-1111-111111111111");
    private static final UUID USER = UUID.fromString("00000000-0000-0000-0000-000000009001");
    private static final UUID OTHER_USER = UUID.fromString("00000000-0000-0000-0000-000000009002");
    private static final UUID PROJECT = UUID.fromString("00000000-0000-0000-0000-000000010001");
    private static final Instant NOW = Instant.parse("2026-07-18T02:00:00Z");
    private static final Clock CLOCK = Clock.fixed(NOW, ZoneOffset.UTC);

    @Mock
    private ActionProposalMapper proposalMapper;
    @Mock
    private WorkOrderMapper workOrderMapper;
    @Mock
    private TenantTransaction transactions;

    private ObjectMapper objectMapper;
    private ActionProposalService service;

    @BeforeAll
    static void initializeMybatisMetadata() {
        MybatisConfiguration configuration = new MybatisConfiguration();
        TableInfoHelper.initTableInfo(new MapperBuilderAssistant(configuration, "work-order-test"), WorkOrderEntity.class);
    }

    @BeforeEach
    void setUp() {
        objectMapper = new ObjectMapper().registerModule(new JavaTimeModule());
        service = new ActionProposalService(proposalMapper, workOrderMapper, transactions, objectMapper, CLOCK);
        lenient().doAnswer(invocation -> ((Supplier<?>) invocation.getArgument(1)).get())
            .when(transactions).required(any(TenantContext.class), any());
        lenient().when(proposalMapper.insert(any(ActionProposalEntity.class))).thenReturn(1);
    }

    @Test
    void createsAuthoritativeCreatePreviewWithNoBeforeSnapshotAndExactExpiry() {
        TenantContext dispatcher = context("DISPATCHER", USER);

        ActionProposalResponse response = service.create(dispatcher, createCommand("CREATE", null, """
            {
              "work_order_no":"WO-20260718-101","title":"Cooling alarm",
              "description":"Inspect cooling loop","project_id":"00000000-0000-0000-0000-000000010001",
              "project_name":"North plant","space_path":"Building A/Floor 2",
              "order_type":"INSPECTION","priority":"HIGH","source":"AI_ASSISTANT",
              "due_at":"2026-07-19T10:00:00"
            }
            """));

        assertThat(response.id()).isNotNull();
        assertThat(response.actionType()).isEqualTo("CREATE");
        assertThat(response.beforeSnapshot()).isNull();
        assertThat(response.expectedVersion()).isZero();
        assertThat(response.expiresAt()).isEqualTo(LocalDateTime.parse("2026-07-18T02:15:00"));
        assertThat(response.afterSnapshot().get("tenant_id").asText()).isEqualTo(TENANT.toString());
        assertThat(response.afterSnapshot().get("created_by").asText()).isEqualTo(USER.toString());
        assertThat(response.afterSnapshot().get("status").asText()).isEqualTo("PENDING_DISPATCH");
        assertThat(response.afterSnapshot().get("version").asLong()).isZero();

        ActionProposalEntity persisted = persisted();
        assertThat(persisted.getTenantId()).isEqualTo(TENANT);
        assertThat(persisted.getRequestedBy()).isEqualTo(USER);
        assertThat(persisted.getBeforeSnapshot().isNull()).isTrue();
        assertThat(persisted.getRiskLevel()).isEqualTo(response.riskLevel());
        assertThat(persisted.getStatus()).isEqualTo("PENDING_CONFIRMATION");
        assertThat(persisted.getExpectedVersion()).isZero();
        assertThat(persisted.getExpiresAt()).isEqualTo(response.expiresAt());
        verify(transactions).required(eq(dispatcher), any());
        verifyNoInteractions(workOrderMapper);
    }

    @Test
    void supportsEveryTargetActionFromAnAuthoritativeReloadWithoutUpdatingTheOrder() {
        List<Scenario> scenarios = List.of(
            new Scenario("ASSIGN", "DISPATCHER", order("PENDING_DISPATCH", null, null),
                "{\"assignee_id\":\"00000000-0000-0000-0000-000000009002\",\"assignee_name\":\"Lin\",\"reason\":\"dispatch\"}", "PENDING_ACCEPTANCE"),
            new Scenario("UPDATE", "DISPATCHER", order("PROCESSING", OTHER_USER, LocalDateTime.parse("2026-07-18T01:30:00")),
                "{\"title\":\"Updated title\",\"priority\":\"CRITICAL\"}", "PROCESSING"),
            new Scenario("ACCEPT", "OPERATOR", order("PENDING_ACCEPTANCE", USER, null), "{}", "PENDING_ACCEPTANCE"),
            new Scenario("START", "OPERATOR", order("PENDING_ACCEPTANCE", USER, LocalDateTime.parse("2026-07-18T01:30:00")), "{}", "PROCESSING"),
            new Scenario("COMPLETE", "OPERATOR", order("PROCESSING", USER, LocalDateTime.parse("2026-07-18T01:30:00")), "{}", "COMPLETED"),
            new Scenario("CLOSE", "QUALITY_REVIEWER", order("COMPLETED", OTHER_USER, LocalDateTime.parse("2026-07-18T01:30:00")), "{}", "CLOSED"),
            new Scenario("CANCEL", "DISPATCHER", order("PROCESSING", OTHER_USER, LocalDateTime.parse("2026-07-18T01:30:00")),
                "{\"reason\":\" Customer request \"}", "CANCELLED")
        );

        for (Scenario scenario : scenarios) {
            when(workOrderMapper.selectOne(any())).thenReturn(scenario.order());
            ActionProposalResponse response = service.create(
                context(scenario.role(), USER),
                createCommand(scenario.action(), scenario.order().getWorkOrderNo(), scenario.parameters())
            );

            assertThat(response.beforeSnapshot().get("title").asText()).isEqualTo("Database title");
            assertThat(response.afterSnapshot().get("status").asText()).isEqualTo(scenario.expectedStatus());
            assertThat(response.expectedVersion()).isEqualTo(7L);
            assertThat(response.afterSnapshot().get("version").asLong()).isEqualTo(8L);
        }

        ArgumentCaptor<Wrapper<WorkOrderEntity>> query = wrapperCaptor();
        verify(workOrderMapper, org.mockito.Mockito.times(scenarios.size())).selectOne(query.capture());
        for (Wrapper<WorkOrderEntity> wrapper : query.getAllValues()) {
            assertThat(wrapper.getSqlSegment()).contains("tenant_id", "project_id IN", "work_order_no");
            assertThat(parameters(wrapper)).contains(TENANT, PROJECT, "WO-20260718-001");
        }
        verify(workOrderMapper, never()).update(any(), any());
        verify(workOrderMapper, never()).updateById(any(WorkOrderEntity.class));
        verify(workOrderMapper, never()).insert(any(WorkOrderEntity.class));
    }

    @Test
    void aiServiceMayCreateAProposalButCannotSupplyAuthorityFields() {
        when(workOrderMapper.selectOne(any())).thenReturn(order("PROCESSING", OTHER_USER,
            LocalDateTime.parse("2026-07-18T01:30:00")));
        ActionProposalResponse response = service.create(
            context("AI_SERVICE", USER),
            createCommand("CANCEL", "WO-20260718-001", "{\"reason\":\"safety\"}")
        );

        assertThat(response.status()).isEqualTo("PENDING_CONFIRMATION");
        assertThat(persisted().getRequestedBy()).isEqualTo(USER);

        assertThatThrownBy(() -> createCommand("CANCEL", "WO-20260718-001",
            "{\"reason\":\"safety\",\"requested_by\":\"00000000-0000-0000-0000-000000009999\"}"))
            .isInstanceOf(InvalidCommandException.class);
    }

    @Test
    void exactRoleChecksRejectNearMatchesAndUnauthorizedCommands() {
        assertThatThrownBy(() -> service.create(
            context("SUPER_DISPATCHER", USER),
            createCommand("UPDATE", "WO-20260718-001", "{\"title\":\"forged\"}")))
            .isInstanceOf(ActionNotPermittedException.class);

        assertThatThrownBy(() -> service.create(
            context("TENANT_ADMIN", USER),
            createCommand("CANCEL", "WO-20260718-001", "{\"reason\":\"admin override\"}")))
            .isInstanceOf(ActionNotPermittedException.class);
    }

    @Test
    void operatorActionsRequireTheCurrentAuthoritativeSelfAssignment() {
        when(workOrderMapper.selectOne(any())).thenReturn(order("PROCESSING", OTHER_USER,
            LocalDateTime.parse("2026-07-18T01:30:00")));

        assertThatThrownBy(() -> service.create(
            context("OPERATOR", USER),
            createCommand("COMPLETE", "WO-20260718-001", "{}")))
            .isInstanceOf(ActionNotPermittedException.class);

        verify(proposalMapper, never()).insert(any(ActionProposalEntity.class));
    }

    @Test
    void invalidStateAndOutOfScopeTargetsUseTheWrittenPublicExceptions() {
        when(workOrderMapper.selectOne(any())).thenReturn(order("CLOSED", OTHER_USER,
            LocalDateTime.parse("2026-07-18T01:30:00")), (WorkOrderEntity) null);

        assertThatThrownBy(() -> service.create(
            context("DISPATCHER", USER),
            createCommand("UPDATE", "WO-20260718-001", "{\"title\":\"late\"}")))
            .isInstanceOf(InvalidStateTransitionException.class);

        assertThatThrownBy(() -> service.create(
            context("DISPATCHER", USER),
            createCommand("UPDATE", "WO-20260718-999", "{\"title\":\"hidden\"}")))
            .isInstanceOf(WorkOrderNotFoundException.class);
    }

    @Test
    void rejectsMalformedOrUnknownActionParametersAsInvalidCommand() {
        List<ActionProposalRequest> requests = List.of(
            request("BOGUS", null, "{}"),
            request("CREATE", null, "{}"),
            request("CREATE", "WO-1", "{}"),
            request("ASSIGN", "WO-1", "{\"assignee_id\":\"not-a-uuid\",\"assignee_name\":\"Lin\",\"reason\":\"x\"}"),
            request("UPDATE", "WO-1", "{}"),
            request("ACCEPT", "WO-1", "{\"status\":\"CLOSED\"}"),
            request("CANCEL", "WO-1", "{\"reason\":\" \"}")
        );

        for (ActionProposalRequest request : requests) {
            assertThatThrownBy(request::toCommand).isInstanceOf(InvalidCommandException.class);
        }
    }

    private CreateProposalCommand createCommand(String action, String target, String parameters) {
        return request(action, target, parameters).toCommand();
    }

    private ActionProposalRequest request(String action, String target, String parameters) {
        try {
            return new ActionProposalRequest(action, target, objectMapper.readTree(parameters));
        } catch (Exception exception) {
            throw new AssertionError(exception);
        }
    }

    private ActionProposalEntity persisted() {
        ArgumentCaptor<ActionProposalEntity> captor = ArgumentCaptor.forClass(ActionProposalEntity.class);
        verify(proposalMapper).insert(captor.capture());
        return captor.getValue();
    }

    private static WorkOrderEntity order(String status, UUID assignee, LocalDateTime acceptedAt) {
        return WorkOrderEntity.builder()
            .id(UUID.fromString("00000000-0000-0000-0000-000000000001"))
            .tenantId(TENANT)
            .projectId(PROJECT)
            .workOrderNo("WO-20260718-001")
            .title("Database title")
            .description("Database description")
            .projectName("North plant")
            .spacePath("Building A")
            .orderType("REPAIR")
            .priority("HIGH")
            .status(status)
            .assigneeId(assignee)
            .assigneeName(assignee == null ? null : "Database assignee")
            .source("MANUAL")
            .version(7L)
            .acceptedAt(acceptedAt)
            .createdAt(LocalDateTime.parse("2026-07-17T10:00:00"))
            .dueAt(LocalDateTime.parse("2026-07-19T10:00:00"))
            .build();
    }

    private static TenantContext context(String role, UUID user) {
        return new TenantContext(TENANT, user, role.toLowerCase(), Set.of(role), Set.of(PROJECT),
            Set.of("work-order:write"), "request-test", "trace-test");
    }

    private static ArgumentCaptor<Wrapper<WorkOrderEntity>> wrapperCaptor() {
        return ArgumentCaptor.forClass((Class) Wrapper.class);
    }

    private static List<Object> parameters(Wrapper<WorkOrderEntity> wrapper) {
        return ((AbstractWrapper<?, ?, ?>) wrapper).getParamNameValuePairs().values().stream().toList();
    }

    private record Scenario(
        String action,
        String role,
        WorkOrderEntity order,
        String parameters,
        String expectedStatus
    ) {
    }
}
