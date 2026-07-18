package com.tangmeng.workorder.command;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.NullNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.tangmeng.workorder.api.ActionProposalResponse;
import com.tangmeng.workorder.command.model.CreateProposalCommand;
import com.tangmeng.workorder.domain.ActionProposalEntity;
import com.tangmeng.workorder.domain.WorkOrderAction;
import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.domain.WorkOrderSnapshot;
import com.tangmeng.workorder.domain.WorkOrderStateMachine;
import com.tangmeng.workorder.domain.WorkOrderStatus;
import com.tangmeng.workorder.domain.WorkOrderTransitionResult;
import com.tangmeng.workorder.mapper.ActionProposalMapper;
import com.tangmeng.workorder.mapper.WorkOrderMapper;
import com.tangmeng.workorder.security.TenantContext;
import com.tangmeng.workorder.service.WorkOrderNotFoundException;
import com.tangmeng.workorder.tenant.TenantTransaction;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.time.Clock;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.Set;
import java.util.UUID;

@Service
@RequiredArgsConstructor
public class ActionProposalService {

    public static final String TENANT_ADMIN = "TENANT_ADMIN";
    public static final String DISPATCHER = "DISPATCHER";
    public static final String OPERATOR = "OPERATOR";
    public static final String QUALITY_REVIEWER = "QUALITY_REVIEWER";
    public static final String AI_SERVICE = "AI_SERVICE";

    private static final Set<String> EFFECTIVE_ROLES = Set.of(
        TENANT_ADMIN, DISPATCHER, OPERATOR, QUALITY_REVIEWER, AI_SERVICE
    );
    private static final String PENDING_CONFIRMATION = "PENDING_CONFIRMATION";

    private final ActionProposalMapper proposalMapper;
    private final WorkOrderMapper workOrderMapper;
    private final TenantTransaction transactions;
    private final ObjectMapper objectMapper;
    private final Clock clock;

    public ActionProposalResponse create(TenantContext context, CreateProposalCommand command) {
        if (context == null || command == null) {
            throw new InvalidCommandException();
        }
        assertKnownRoles(context);
        return transactions.required(context, () -> createInsideTransaction(context, command));
    }

    private ActionProposalResponse createInsideTransaction(
        TenantContext context,
        CreateProposalCommand command
    ) {
        LocalDateTime now = LocalDateTime.ofInstant(clock.instant(), ZoneOffset.UTC);
        UUID proposalId = UUID.randomUUID();
        JsonNode before;
        JsonNode after;
        UUID targetId;
        long expectedVersion;

        if (command instanceof CreateProposalCommand.Create create) {
            requireRoleOrAi(context, DISPATCHER);
            if (!context.projectIds().contains(create.projectId())) {
                throw new WorkOrderNotFoundException(create.workOrderNo());
            }
            targetId = null;
            expectedVersion = 0L;
            before = NullNode.getInstance();
            after = createSnapshot(context, create, now);
        } else {
            requireActionRole(context, command);
            WorkOrderEntity current = loadTarget(context, command.targetWorkOrderNo());
            requireSelfAssignment(context, command, current);
            targetId = current.getId();
            expectedVersion = current.getVersion();
            before = snapshot(current);
            after = preview(current, command, now);
        }

        String risk = riskFor(command.actionType());
        LocalDateTime expiresAt = now.plusMinutes(15);
        ActionProposalEntity entity = ActionProposalEntity.builder()
            .id(proposalId)
            .tenantId(context.tenantId())
            .actionType(command.actionType())
            .targetId(targetId)
            .commandPayload(objectMapper.valueToTree(command))
            .beforeSnapshot(before)
            .afterSnapshot(after)
            .riskLevel(risk)
            .status(PENDING_CONFIRMATION)
            .requestedBy(context.userId())
            .expectedVersion(expectedVersion)
            .expiresAt(expiresAt)
            .createdAt(now)
            .updatedAt(now)
            .build();
        if (proposalMapper.insert(entity) != 1) {
            throw new IllegalStateException("Action proposal was not persisted");
        }

        return new ActionProposalResponse(
            proposalId,
            command.actionType(),
            command.targetWorkOrderNo(),
            risk,
            PENDING_CONFIRMATION,
            before.isNull() ? null : before,
            after,
            expectedVersion,
            expiresAt
        );
    }

    private WorkOrderEntity loadTarget(TenantContext context, String workOrderNo) {
        if (context.projectIds().isEmpty()) {
            throw new WorkOrderNotFoundException(workOrderNo);
        }
        LambdaQueryWrapper<WorkOrderEntity> query = Wrappers.lambdaQuery();
        query.eq(WorkOrderEntity::getTenantId, context.tenantId())
            .in(WorkOrderEntity::getProjectId, context.projectIds())
            .eq(WorkOrderEntity::getWorkOrderNo, workOrderNo);
        WorkOrderEntity current = workOrderMapper.selectOne(query);
        if (current == null) {
            throw new WorkOrderNotFoundException(workOrderNo);
        }
        return current;
    }

    private JsonNode preview(
        WorkOrderEntity current,
        CreateProposalCommand command,
        LocalDateTime occurredAt
    ) {
        WorkOrderStateMachine stateMachine = new WorkOrderStateMachine();
        ObjectNode after = (ObjectNode) snapshot(current).deepCopy();

        if (command instanceof CreateProposalCommand.Update update) {
            stateMachine.assertMutable(status(current));
            putIfPresent(after, "title", update.title());
            putIfPresent(after, "description", update.description());
            putIfPresent(after, "priority", update.priority());
            putIfPresent(after, "due_at", update.dueAt());
        } else if (command instanceof CreateProposalCommand.Assign assign) {
            WorkOrderTransitionResult transition = transition(current, WorkOrderAction.ASSIGN, null, occurredAt);
            after.put("status", transition.status().name());
            after.put("assignee_id", assign.assigneeId().toString());
            after.put("assignee_name", assign.assigneeName());
        } else if (command instanceof CreateProposalCommand.Accept) {
            WorkOrderTransitionResult transition = transition(current, WorkOrderAction.ACCEPT, null, occurredAt);
            after.put("status", transition.status().name());
            putIfPresent(after, "accepted_at", transition.acceptedAt());
        } else if (command instanceof CreateProposalCommand.Start) {
            WorkOrderTransitionResult transition = transition(current, WorkOrderAction.START, null, occurredAt);
            after.put("status", transition.status().name());
        } else if (command instanceof CreateProposalCommand.Complete) {
            WorkOrderTransitionResult transition = transition(current, WorkOrderAction.COMPLETE, null, occurredAt);
            after.put("status", transition.status().name());
            putIfPresent(after, "completed_at", occurredAt);
        } else if (command instanceof CreateProposalCommand.Close) {
            WorkOrderTransitionResult transition = transition(current, WorkOrderAction.CLOSE, null, occurredAt);
            after.put("status", transition.status().name());
        } else if (command instanceof CreateProposalCommand.Cancel cancel) {
            WorkOrderTransitionResult transition = transition(current, WorkOrderAction.CANCEL, cancel.reason(), occurredAt);
            after.put("status", transition.status().name());
            after.put("cancel_reason", transition.cancelReason());
            putIfPresent(after, "cancelled_at", occurredAt);
        } else {
            throw new InvalidCommandException();
        }

        after.put("version", current.getVersion() + 1L);
        return after;
    }

    private WorkOrderTransitionResult transition(
        WorkOrderEntity current,
        WorkOrderAction action,
        String cancelReason,
        LocalDateTime occurredAt
    ) {
        return new WorkOrderStateMachine().transition(
            new WorkOrderSnapshot(status(current), current.getAcceptedAt(), cancelReason),
            action,
            occurredAt
        );
    }

    private static WorkOrderStatus status(WorkOrderEntity current) {
        try {
            return WorkOrderStatus.valueOf(current.getStatus());
        } catch (RuntimeException exception) {
            throw new InvalidCommandException(exception);
        }
    }

    private ObjectNode createSnapshot(
        TenantContext context,
        CreateProposalCommand.Create create,
        LocalDateTime now
    ) {
        ObjectNode snapshot = objectMapper.createObjectNode();
        snapshot.put("id", UUID.randomUUID().toString());
        snapshot.put("tenant_id", context.tenantId().toString());
        snapshot.put("work_order_no", create.workOrderNo());
        snapshot.put("title", create.title());
        snapshot.put("description", create.description());
        snapshot.put("project_id", create.projectId().toString());
        snapshot.put("project_name", create.projectName());
        snapshot.put("space_path", create.spacePath());
        snapshot.put("order_type", create.orderType());
        snapshot.put("priority", create.priority());
        snapshot.put("status", WorkOrderStatus.PENDING_DISPATCH.name());
        snapshot.put("source", create.source());
        snapshot.put("version", 0L);
        snapshot.put("created_by", context.userId().toString());
        snapshot.put("updated_by", context.userId().toString());
        putIfPresent(snapshot, "created_at", now);
        putIfPresent(snapshot, "due_at", create.dueAt());
        return snapshot;
    }

    private ObjectNode snapshot(WorkOrderEntity entity) {
        ObjectNode snapshot = objectMapper.createObjectNode();
        putIfPresent(snapshot, "id", entity.getId());
        putIfPresent(snapshot, "tenant_id", entity.getTenantId());
        putIfPresent(snapshot, "work_order_no", entity.getWorkOrderNo());
        putIfPresent(snapshot, "title", entity.getTitle());
        putIfPresent(snapshot, "description", entity.getDescription());
        putIfPresent(snapshot, "project_id", entity.getProjectId());
        putIfPresent(snapshot, "project_name", entity.getProjectName());
        putIfPresent(snapshot, "space_path", entity.getSpacePath());
        putIfPresent(snapshot, "order_type", entity.getOrderType());
        putIfPresent(snapshot, "priority", entity.getPriority());
        putIfPresent(snapshot, "status", entity.getStatus());
        putIfPresent(snapshot, "assignee_id", entity.getAssigneeId());
        putIfPresent(snapshot, "assignee_name", entity.getAssigneeName());
        putIfPresent(snapshot, "source", entity.getSource());
        putIfPresent(snapshot, "root_work_order_id", entity.getRootWorkOrderId());
        putIfPresent(snapshot, "root_work_order_no", entity.getRootWorkOrderNo());
        putIfPresent(snapshot, "rework_reason", entity.getReworkReason());
        snapshot.put("version", entity.getVersion());
        putIfPresent(snapshot, "accepted_at", entity.getAcceptedAt());
        putIfPresent(snapshot, "created_by", entity.getCreatedBy());
        putIfPresent(snapshot, "updated_by", entity.getUpdatedBy());
        putIfPresent(snapshot, "created_at", entity.getCreatedAt());
        putIfPresent(snapshot, "due_at", entity.getDueAt());
        putIfPresent(snapshot, "completed_at", entity.getCompletedAt());
        putIfPresent(snapshot, "cancelled_at", entity.getCancelledAt());
        putIfPresent(snapshot, "cancel_reason", entity.getCancelReason());
        return snapshot;
    }

    private static void putIfPresent(ObjectNode node, String field, Object value) {
        if (value instanceof UUID uuid) {
            node.put(field, uuid.toString());
        } else if (value instanceof LocalDateTime dateTime) {
            node.put(field, dateTime.toString());
        } else if (value instanceof String text) {
            node.put(field, text);
        }
    }

    private static void assertKnownRoles(TenantContext context) {
        if (context.roles().stream().noneMatch(EFFECTIVE_ROLES::contains)) {
            throw new ActionNotPermittedException();
        }
    }

    private static void requireActionRole(TenantContext context, CreateProposalCommand command) {
        if (context.roles().contains(AI_SERVICE)) {
            return;
        }
        String required = switch (command.actionType()) {
            case "ASSIGN", "UPDATE", "CANCEL" -> DISPATCHER;
            case "ACCEPT", "START", "COMPLETE" -> OPERATOR;
            case "CLOSE" -> QUALITY_REVIEWER;
            default -> throw new InvalidCommandException();
        };
        requireRole(context, required);
    }

    private static void requireRoleOrAi(TenantContext context, String role) {
        if (!context.roles().contains(AI_SERVICE)) {
            requireRole(context, role);
        }
    }

    private static void requireRole(TenantContext context, String role) {
        if (!context.roles().contains(role)) {
            throw new ActionNotPermittedException();
        }
    }

    private static void requireSelfAssignment(
        TenantContext context,
        CreateProposalCommand command,
        WorkOrderEntity current
    ) {
        if (context.roles().contains(AI_SERVICE)) {
            return;
        }
        if ((command instanceof CreateProposalCommand.Accept
            || command instanceof CreateProposalCommand.Start
            || command instanceof CreateProposalCommand.Complete)
            && !context.userId().equals(current.getAssigneeId())) {
            throw new ActionNotPermittedException();
        }
    }

    private static String riskFor(String actionType) {
        return switch (actionType) {
            case "UPDATE", "ACCEPT" -> "LOW";
            case "CREATE", "ASSIGN", "START" -> "MEDIUM";
            case "COMPLETE", "CLOSE", "CANCEL" -> "HIGH";
            default -> throw new InvalidCommandException();
        };
    }
}
