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
import com.tangmeng.workorder.domain.ProjectEntity;
import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.mapper.ActionProposalMapper;
import com.tangmeng.workorder.mapper.ProjectMapper;
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
import java.util.LinkedHashSet;
import java.util.Map;
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
    private ProjectMapper projectMapper;
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
        TableInfoHelper.initTableInfo(new MapperBuilderAssistant(configuration, "project-test"), ProjectEntity.class);
    }

    @BeforeEach
    void setUp() {
        objectMapper = new ObjectMapper().registerModule(new JavaTimeModule());
        service = new ActionProposalService(proposalMapper, projectMapper, workOrderMapper, transactions, objectMapper, CLOCK);
        lenient().doAnswer(invocation -> ((Supplier<?>) invocation.getArgument(1)).get())
            .when(transactions).required(any(TenantContext.class), any());
        lenient().when(proposalMapper.insert(any(ActionProposalEntity.class))).thenReturn(1);
        lenient().when(projectMapper.selectOne(any())).thenReturn(ProjectEntity.builder()
            .id(PROJECT).tenantId(TENANT).projectKey("north").name("Authoritative North Plant")
            .status("ACTIVE").build());
    }

    @Test
    void createsAuthoritativeCreatePreviewWithNoBeforeSnapshotAndExactExpiry() {
        TenantContext dispatcher = context("DISPATCHER", USER);

        ActionProposalResponse response = service.create(dispatcher, createCommand("CREATE", null, """
            {
              "work_order_no":"WO-20260718-101","title":"Cooling alarm",
              "description":"Inspect cooling loop","project_id":"00000000-0000-0000-0000-000000010001",
              "space_path":"Building A/Floor 2",
              "order_type":"INSPECTION","priority":"HIGH","source":"AI_ASSISTANT",
              "due_at":"2026-07-19T10:00:00"
            }
            """));

        assertThat(response.id()).isNotNull();
        assertThat(response.actionType()).isEqualTo("CREATE");
        assertThat(response.beforeSnapshot()).isSameAs(com.fasterxml.jackson.databind.node.NullNode.getInstance());
        assertThat(response.expectedVersion()).isZero();
        assertThat(response.expiresAt()).isEqualTo(LocalDateTime.parse("2026-07-18T02:15:00"));
        assertThat(response.afterSnapshot().get("tenant_id").asText()).isEqualTo(TENANT.toString());
        assertThat(response.afterSnapshot().get("created_by").asText()).isEqualTo(USER.toString());
        assertThat(response.afterSnapshot().get("project_name").asText()).isEqualTo("Authoritative North Plant");
        assertThat(response.afterSnapshot().get("status").asText()).isEqualTo("PENDING_DISPATCH");
        assertThat(response.afterSnapshot().get("version").asLong()).isZero();
        assertThat(response.afterSnapshot().get("work_order_no").asText()).isEqualTo("WO-20260718-101");
        assertThat(response.afterSnapshot().get("title").asText()).isEqualTo("Cooling alarm");
        assertThat(response.afterSnapshot().get("description").asText()).isEqualTo("Inspect cooling loop");
        assertThat(response.afterSnapshot().get("project_id").asText()).isEqualTo(PROJECT.toString());
        assertThat(response.afterSnapshot().get("space_path").asText()).isEqualTo("Building A/Floor 2");
        assertThat(response.afterSnapshot().get("order_type").asText()).isEqualTo("INSPECTION");
        assertThat(response.afterSnapshot().get("priority").asText()).isEqualTo("HIGH");
        assertThat(response.afterSnapshot().get("source").asText()).isEqualTo("AI_ASSISTANT");

        ActionProposalEntity persisted = persisted();
        assertThat(persisted.getTenantId()).isEqualTo(TENANT);
        assertThat(persisted.getRequestedBy()).isEqualTo(USER);
        assertThat(persisted.getCommandPayload().get("project_name").asText())
            .isEqualTo("Authoritative North Plant");
        assertThat(persisted.getBeforeSnapshot().isNull()).isTrue();
        assertThat(persisted.getRiskLevel()).isEqualTo(response.riskLevel());
        assertThat(persisted.getStatus()).isEqualTo("PENDING_CONFIRMATION");
        assertThat(persisted.getExpectedVersion()).isZero();
        assertThat(persisted.getExpiresAt()).isEqualTo(response.expiresAt());
        verify(transactions).required(eq(dispatcher), any());
        ArgumentCaptor<Wrapper<ProjectEntity>> projectQuery = ArgumentCaptor.forClass((Class) Wrapper.class);
        verify(projectMapper).selectOne(projectQuery.capture());
        assertThat(projectQuery.getValue().getSqlSegment()).contains("tenant_id", "id", "status");
        assertThat(projectParameters(projectQuery.getValue())).contains(TENANT, PROJECT, "ACTIVE");
        verifyNoInteractions(workOrderMapper);
    }

    @Test
    void createFailsClosedForMissingInactiveCrossTenantOrOutOfScopeProject() {
        when(projectMapper.selectOne(any())).thenReturn(null);

        assertThatThrownBy(() -> service.create(context("DISPATCHER", USER), createCommand("CREATE", null, """
            {"work_order_no":"WO-404","title":"x","description":"x",
             "project_id":"00000000-0000-0000-0000-000000010001","space_path":"x",
             "order_type":"x","priority":"LOW","source":"x","due_at":"2026-07-19T10:00:00"}
            """))).isInstanceOf(WorkOrderNotFoundException.class);

        TenantContext noScope = new TenantContext(TENANT, USER, "dispatcher", Set.of("DISPATCHER"), Set.of(),
            Set.of("work-order:write"), "request-test", "trace-test");
        assertThatThrownBy(() -> service.create(noScope, createCommand("CREATE", null, """
            {"work_order_no":"WO-HIDDEN","title":"x","description":"x",
             "project_id":"00000000-0000-0000-0000-000000010001","space_path":"x",
             "order_type":"x","priority":"LOW","source":"x","due_at":"2026-07-19T10:00:00"}
            """))).isInstanceOf(WorkOrderNotFoundException.class);

        verify(projectMapper).selectOne(any());
        verify(proposalMapper, never()).insert(any(ActionProposalEntity.class));
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
            assertThat(changedFields(response.beforeSnapshot(), response.afterSnapshot()))
                .containsExactlyInAnyOrderElementsOf(expectedChangedFields(scenario.action()));
            assertActionDelta(scenario.action(), response.afterSnapshot());
            assertThat(response.afterSnapshot().get("description").asText()).isEqualTo("Database description");
            assertThat(response.afterSnapshot().get("project_name").asText()).isEqualTo("North plant");
            assertThat(response.afterSnapshot().get("space_path").asText()).isEqualTo("Building A");
            assertThat(response.afterSnapshot().get("order_type").asText()).isEqualTo("REPAIR");
            assertThat(response.afterSnapshot().get("source").asText()).isEqualTo("MANUAL");
            assertThat(response.afterSnapshot().get("due_at").asText()).isEqualTo("2026-07-19T10:00");
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
    void aiServiceMayCreateEveryProposalTypeButCannotSupplyAuthorityFields() {
        when(workOrderMapper.selectOne(any())).thenReturn(
            order("PENDING_DISPATCH", null, null),
            order("PROCESSING", OTHER_USER, LocalDateTime.parse("2026-07-18T01:30:00")),
            order("PENDING_ACCEPTANCE", OTHER_USER, null),
            order("PENDING_ACCEPTANCE", OTHER_USER, LocalDateTime.parse("2026-07-18T01:30:00")),
            order("PROCESSING", OTHER_USER, LocalDateTime.parse("2026-07-18T01:30:00")),
            order("COMPLETED", OTHER_USER, LocalDateTime.parse("2026-07-18T01:30:00")),
            order("PROCESSING", OTHER_USER, LocalDateTime.parse("2026-07-18T01:30:00"))
        );
        List<CreateProposalCommand> commands = List.of(
            createCommand("CREATE", null, """
                {"work_order_no":"WO-AI","title":"x","description":"x",
                 "project_id":"00000000-0000-0000-0000-000000010001","space_path":"x",
                 "order_type":"x","priority":"LOW","source":"AI","due_at":"2026-07-19T10:00:00"}
                """),
            createCommand("ASSIGN", "WO-20260718-001",
                "{\"assignee_id\":\"00000000-0000-0000-0000-000000009002\",\"assignee_name\":\"Lin\",\"reason\":\"x\"}"),
            createCommand("UPDATE", "WO-20260718-001", "{\"title\":\"x\"}"),
            createCommand("ACCEPT", "WO-20260718-001", "{}"),
            createCommand("START", "WO-20260718-001", "{}"),
            createCommand("COMPLETE", "WO-20260718-001", "{}"),
            createCommand("CLOSE", "WO-20260718-001", "{}"),
            createCommand("CANCEL", "WO-20260718-001", "{\"reason\":\"safety\"}")
        );

        for (CreateProposalCommand command : commands) {
            assertThat(service.create(context("AI_SERVICE", USER), command).status())
                .isEqualTo("PENDING_CONFIRMATION");
        }
        ArgumentCaptor<ActionProposalEntity> proposals = ArgumentCaptor.forClass(ActionProposalEntity.class);
        verify(proposalMapper, org.mockito.Mockito.times(8)).insert(proposals.capture());
        assertThat(proposals.getAllValues()).allMatch(proposal -> proposal.getRequestedBy().equals(USER));

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
    void rejectsEveryActionForTheWrongRoleAndEveryAdminOverride() {
        List<RoleScenario> scenarios = List.of(
            new RoleScenario("CREATE", "OPERATOR", null, """
                {"work_order_no":"WO-X","title":"x","description":"x",
                 "project_id":"00000000-0000-0000-0000-000000010001","space_path":"x",
                 "order_type":"x","priority":"LOW","source":"x","due_at":"2026-07-19T10:00:00"}
                """),
            new RoleScenario("ASSIGN", "OPERATOR", "WO-20260718-001",
                "{\"assignee_id\":\"00000000-0000-0000-0000-000000009002\",\"assignee_name\":\"Lin\",\"reason\":\"x\"}"),
            new RoleScenario("UPDATE", "QUALITY_REVIEWER", "WO-20260718-001", "{\"title\":\"x\"}"),
            new RoleScenario("CANCEL", "OPERATOR", "WO-20260718-001", "{\"reason\":\"x\"}"),
            new RoleScenario("ACCEPT", "DISPATCHER", "WO-20260718-001", "{}"),
            new RoleScenario("START", "QUALITY_REVIEWER", "WO-20260718-001", "{}"),
            new RoleScenario("COMPLETE", "DISPATCHER", "WO-20260718-001", "{}"),
            new RoleScenario("CLOSE", "DISPATCHER", "WO-20260718-001", "{}")
        );

        for (RoleScenario scenario : scenarios) {
            CreateProposalCommand command = createCommand(
                scenario.action(), scenario.target(), scenario.parameters());
            assertThatThrownBy(() -> service.create(context(scenario.wrongRole(), USER), command))
                .as("%s must reject %s", scenario.action(), scenario.wrongRole())
                .isInstanceOf(ActionNotPermittedException.class);
            assertThatThrownBy(() -> service.create(context("TENANT_ADMIN", USER), command))
                .as("%s must reject undocumented admin override", scenario.action())
                .isInstanceOf(ActionNotPermittedException.class);
        }

        Map<String, CreateProposalCommand> nearMisses = Map.of(
            "DISPATCHER_EXTRA", createCommand("UPDATE", "WO-20260718-001", "{\"title\":\"x\"}"),
            "OPERATOR_LEAD", createCommand("COMPLETE", "WO-20260718-001", "{}"),
            "QUALITY_REVIEWER_BACKUP", createCommand("CLOSE", "WO-20260718-001", "{}")
        );
        nearMisses.forEach((role, command) -> assertThatThrownBy(
            () -> service.create(context(role, USER), command))
            .isInstanceOf(ActionNotPermittedException.class));

        verify(proposalMapper, never()).insert(any(ActionProposalEntity.class));
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
            request("CREATE", null, """
                {"work_order_no":"WO-1","title":"x","description":"x",
                 "project_id":"00000000-0000-0000-0000-000000010001",
                 "project_name":"forged","space_path":"x","order_type":"x",
                 "priority":"LOW","source":"x","due_at":"2026-07-19T10:00:00"}
                """),
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

    private static List<Object> projectParameters(Wrapper<ProjectEntity> wrapper) {
        return ((AbstractWrapper<?, ?, ?>) wrapper).getParamNameValuePairs().values().stream().toList();
    }

    private static Set<String> changedFields(JsonNode before, JsonNode after) {
        Set<String> fields = new LinkedHashSet<>();
        before.fieldNames().forEachRemaining(fields::add);
        after.fieldNames().forEachRemaining(fields::add);
        fields.removeIf(field -> java.util.Objects.equals(before.get(field), after.get(field)));
        return fields;
    }

    private static Set<String> expectedChangedFields(String action) {
        return switch (action) {
            case "ASSIGN" -> Set.of("status", "assignee_id", "assignee_name", "version");
            case "UPDATE" -> Set.of("title", "priority", "version");
            case "ACCEPT" -> Set.of("accepted_at", "version");
            case "START", "CLOSE" -> Set.of("status", "version");
            case "COMPLETE" -> Set.of("status", "completed_at", "version");
            case "CANCEL" -> Set.of("status", "cancel_reason", "cancelled_at", "version");
            default -> throw new AssertionError(action);
        };
    }

    private static void assertActionDelta(String action, JsonNode after) {
        switch (action) {
            case "ASSIGN" -> {
                assertThat(after.get("assignee_id").asText()).isEqualTo(OTHER_USER.toString());
                assertThat(after.get("assignee_name").asText()).isEqualTo("Lin");
            }
            case "UPDATE" -> {
                assertThat(after.get("title").asText()).isEqualTo("Updated title");
                assertThat(after.get("priority").asText()).isEqualTo("CRITICAL");
            }
            case "ACCEPT" -> assertThat(after.get("accepted_at").asText()).isEqualTo("2026-07-18T02:00");
            case "START", "CLOSE" -> { }
            case "COMPLETE" -> assertThat(after.get("completed_at").asText()).isEqualTo("2026-07-18T02:00");
            case "CANCEL" -> {
                assertThat(after.get("cancel_reason").asText()).isEqualTo("Customer request");
                assertThat(after.get("cancelled_at").asText()).isEqualTo("2026-07-18T02:00");
            }
            default -> throw new AssertionError(action);
        }
    }

    private record Scenario(
        String action,
        String role,
        WorkOrderEntity order,
        String parameters,
        String expectedStatus
    ) {
    }

    private record RoleScenario(String action, String wrongRole, String target, String parameters) {
    }
}
