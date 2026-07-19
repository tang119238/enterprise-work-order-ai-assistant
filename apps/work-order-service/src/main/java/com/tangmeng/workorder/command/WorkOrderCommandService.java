package com.tangmeng.workorder.command;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.NullNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.tangmeng.workorder.api.WorkOrderExecutionResponse;
import com.tangmeng.workorder.domain.ActionProposalEntity;
import com.tangmeng.workorder.domain.ProjectEntity;
import com.tangmeng.workorder.domain.WorkOrderAction;
import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.domain.WorkOrderEventEntity;
import com.tangmeng.workorder.domain.WorkOrderSnapshot;
import com.tangmeng.workorder.domain.WorkOrderStateMachine;
import com.tangmeng.workorder.domain.WorkOrderStatus;
import com.tangmeng.workorder.domain.WorkOrderTransitionResult;
import com.tangmeng.workorder.security.TenantContext;
import com.tangmeng.workorder.service.InvalidStateTransitionException;
import com.tangmeng.workorder.service.WorkOrderNotFoundException;
import com.tangmeng.workorder.tenant.TenantAccessService;
import com.tangmeng.workorder.tenant.TenantTransaction;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Clock;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

@Service
@RequiredArgsConstructor
public class WorkOrderCommandService {

    public static final String CONFIRM_OPERATION = "CONFIRM_ACTION_PROPOSAL";

    private final WorkOrderCommandRepository repository;
    private final TenantTransaction transactions;
    private final TenantAccessService access;
    private final ObjectMapper objectMapper;
    private final Clock clock;

    public WorkOrderExecutionResponse execute(
        TenantContext context,
        ActionProposalEntity proposal,
        String idempotencyKey
    ) {
        if (context == null || proposal == null || proposal.getId() == null
            || idempotencyKey == null || idempotencyKey.isBlank()) {
            throw new InvalidCommandException();
        }
        String key = idempotencyKey.strip();
        if (key.length() > 200) throw new InvalidCommandException();
        String hash = requestHash(objectMapper, proposal.getId(), proposal.getCommandPayload());
        ExecutionAttempt attempt = new ExecutionAttempt(proposal.getId());
        try {
            return transactions.required(context,
                () -> executeInside(context, proposal.getId(), key, hash, attempt));
        } catch (ActionProposalExpiredException exception) {
            recoverExpired(context, proposal.getId());
            throw exception;
        } catch (IdempotencyConflictException exception) {
            throw exception;
        } catch (RuntimeException exception) {
            if (attempt.recoverable()) {
                recoverFailure(context, attempt, errorCode(exception));
            }
            throw exception;
        }
    }

    public boolean reject(TenantContext context, UUID proposalId) {
        if (context == null || proposalId == null) throw new InvalidCommandException();
        if (context.roles().contains(ActionProposalService.AI_SERVICE)) {
            throw new ActionNotPermittedException();
        }
        try {
            return transactions.required(context, () -> {
                LocalDateTime now = now();
                ActionProposalEntity proposal = requireProposalRecord(context, proposalId);
                CurrentAuthority authority = currentAuthority(context);
                authorize(context, proposal, authority.roles(), authority.projects());
                if (isExpired(proposal, now)) throw new ActionProposalExpiredException();
                requireEligible(proposal);
                recheckTargetAccess(context, proposal, authority.projects());
                if (!repository.rejectProposal(context.tenantId(), proposalId, context.userId(), now)) {
                    throw new InvalidCommandException();
                }
                return true;
            });
        } catch (ActionProposalExpiredException exception) {
            recoverExpired(context, proposalId);
            throw exception;
        }
    }

    private WorkOrderExecutionResponse executeInside(
        TenantContext context,
        UUID proposalId,
        String key,
        String hash,
        ExecutionAttempt attempt
    ) {
        LocalDateTime now = now();
        Optional<WorkOrderCommandRepository.StoredIdempotency> stored =
            repository.findIdempotency(context.tenantId(), CONFIRM_OPERATION, key);
        if (stored.isEmpty()
            && !repository.reserveIdempotency(context.tenantId(), CONFIRM_OPERATION, key, hash, now)) {
            stored = repository.findIdempotency(context.tenantId(), CONFIRM_OPERATION, key);
            if (stored.isEmpty()) throw new InvalidCommandException();
        }
        ActionProposalEntity proposal = requireProposalRecord(context, proposalId);
        if (isExpired(proposal, now)) throw new ActionProposalExpiredException();
        CurrentAuthority authority = currentAuthority(context);
        authorize(context, proposal, authority.roles(), authority.projects());
        loadAuthorizedTarget(context, proposal, authority.projects());
        attempt.authorized(context.userId());

        if (stored.isPresent()) {
            return replay(stored.get(), hash);
        }

        requireEligible(proposal);

        if (!repository.claimProposal(context.tenantId(), proposalId, context.userId(), now)) {
            throw new InvalidCommandException();
        }
        attempt.claimed();

        AuthorizedTarget facts = loadAuthorizedTarget(context, proposal, authority.projects());
        WorkOrderEntity current = facts.workOrder();
        ProjectEntity project = facts.project();
        if ("CREATE".equals(proposal.getActionType())) {
            if (!Objects.equals(proposal.getExpectedVersion(), 0L)) {
                throw new WorkOrderVersionConflictException(proposal.getAfterSnapshot());
            }
        } else {
            if (!Objects.equals(proposal.getExpectedVersion(), current.getVersion())) {
                throw new WorkOrderVersionConflictException(preview(context, current, proposal, now));
            }
        }

        JsonNode before = current == null ? NullNode.getInstance() : snapshot(current);
        WorkOrderEntity changed = current == null
            ? createOrder(context, proposal, project, now)
            : apply(context, current, proposal, now);
        if (current == null) {
            WorkOrderCommandRepository.InsertWorkOrderResult result = repository.insertWorkOrder(changed);
            if (result == WorkOrderCommandRepository.InsertWorkOrderResult.DUPLICATE) {
                WorkOrderEntity duplicate = repository.findWorkOrderByIdentity(
                    context.tenantId(), changed.getId(), changed.getWorkOrderNo(), authority.projects());
                if (duplicate == null) throw new InvalidCommandException();
                throw new WorkOrderVersionConflictException(snapshot(duplicate));
            }
            if (result != WorkOrderCommandRepository.InsertWorkOrderResult.INSERTED) {
                throw new InvalidCommandException();
            }
        } else if (!repository.updateWorkOrder(changed, current.getVersion())) {
            WorkOrderEntity fresh = repository.findWorkOrder(
                context.tenantId(), current.getId(), authority.projects());
            if (fresh == null) throw new WorkOrderNotFoundException("hidden");
            throw new WorkOrderVersionConflictException(freshConflictPreview(context, fresh, proposal, now));
        }

        if (current != null && !Objects.equals(current.getAssigneeId(), changed.getAssigneeId())) {
            int closed = repository.closeOpenAssignment(context.tenantId(), changed.getId(), now);
            int expectedClosed = current.getAssigneeId() == null ? 0 : 1;
            if (closed != expectedClosed) throw new InvalidCommandException();
            if (changed.getAssigneeId() != null) {
                requireOne(repository.insertAssignment(context.tenantId(), changed.getId(), changed.getAssigneeId(),
                    text(proposal.getCommandPayload(), "reason"), context.userId(), now));
            }
        }

        JsonNode after = snapshot(changed);
        String eventType = switch (proposal.getActionType()) {
            case "CREATE" -> "WORK_ORDER_CREATED";
            case "ASSIGN" -> "WORK_ORDER_ASSIGNED";
            case "UPDATE" -> "WORK_ORDER_UPDATED";
            case "ACCEPT" -> "WORK_ORDER_ACCEPTED";
            case "START" -> "WORK_ORDER_STARTED";
            case "COMPLETE" -> "WORK_ORDER_COMPLETED";
            case "CLOSE" -> "WORK_ORDER_CLOSED";
            case "CANCEL" -> "WORK_ORDER_CANCELLED";
            default -> throw new InvalidCommandException();
        };
        requireOne(repository.insertEvent(WorkOrderEventEntity.builder()
            .id(UUID.randomUUID()).tenantId(context.tenantId()).workOrderId(changed.getId())
            .eventType(eventType).commandType(proposal.getActionType())
            .beforeSnapshot(before).afterSnapshot(after).actorId(context.userId())
            .requestId(context.requestId()).traceId(context.traceId()).createdAt(now).build()));
        JsonNode outboxPayload = after;
        if ("WORK_ORDER_COMPLETED".equals(eventType)) {
            ObjectNode qualityPayload = after.deepCopy();
            qualityPayload.set("attachments_summary", objectMapper.createArrayNode());
            qualityPayload.put("inspection_round", 1);
            outboxPayload = qualityPayload;
        }
        requireOne(repository.insertOutbox(
            context.tenantId(), changed.getId(), eventType, outboxPayload, now
        ));

        WorkOrderExecutionResponse response = new WorkOrderExecutionResponse(
            proposalId, changed.getId(), changed.getWorkOrderNo(), proposal.getActionType(),
            changed.getStatus(), changed.getVersion());
        JsonNode responseJson = objectMapper.valueToTree(response);
        if (!repository.completeIdempotency(
            context.tenantId(), CONFIRM_OPERATION, key, hash, responseJson, 200)) {
            throw new IllegalStateException("Idempotency completion failed");
        }
        if (!repository.markProposalExecuted(context.tenantId(), proposalId, responseJson, now)) {
            throw new IllegalStateException("Proposal completion failed");
        }
        return response;
    }

    private CurrentAuthority currentAuthority(TenantContext context) {
        UUID currentUser = access.loadCurrentUserId(context.tenantId(), context.subject());
        if (!context.userId().equals(currentUser)) throw new ActionNotPermittedException();
        Set<String> currentRoles = access.loadCurrentRoles(context.tenantId(), context.subject());
        Set<UUID> currentProjects = access.loadCurrentProjects(context.tenantId(), context.subject());
        return new CurrentAuthority(
            context.roles().stream().filter(currentRoles::contains).collect(java.util.stream.Collectors.toUnmodifiableSet()),
            context.projectIds().stream().filter(currentProjects::contains).collect(java.util.stream.Collectors.toUnmodifiableSet())
        );
    }

    private void authorize(TenantContext context, ActionProposalEntity proposal,
                           Set<String> roles, Set<UUID> projects) {
        if (context.roles().contains(ActionProposalService.AI_SERVICE)
            || roles.contains(ActionProposalService.AI_SERVICE)) throw new ActionNotPermittedException();
        String required = switch (proposal.getActionType()) {
            case "CREATE", "ASSIGN", "UPDATE", "CANCEL" -> ActionProposalService.DISPATCHER;
            case "ACCEPT", "START", "COMPLETE" -> ActionProposalService.OPERATOR;
            case "CLOSE" -> ActionProposalService.QUALITY_REVIEWER;
            default -> throw new InvalidCommandException();
        };
        if (!roles.contains(required)) throw new ActionNotPermittedException();
        if (projects.isEmpty()) throw new WorkOrderNotFoundException("hidden");
    }

    private ActionProposalEntity requireProposalRecord(TenantContext context, UUID proposalId) {
        ActionProposalEntity proposal = repository.findProposal(context.tenantId(), proposalId);
        if (proposal == null || !context.tenantId().equals(proposal.getTenantId())) {
            throw new WorkOrderNotFoundException("proposal");
        }
        return proposal;
    }

    private void requireEligible(ActionProposalEntity proposal) {
        if (!Set.of("PENDING_CONFIRMATION", "CONFIRMED").contains(proposal.getStatus())) {
            throw new InvalidCommandException();
        }
    }

    private boolean isExpired(ActionProposalEntity proposal, LocalDateTime now) {
        return "EXPIRED".equals(proposal.getStatus())
            || (Set.of("PENDING_CONFIRMATION", "CONFIRMED").contains(proposal.getStatus())
                && (proposal.getExpiresAt() == null || !proposal.getExpiresAt().isAfter(now)));
    }

    private WorkOrderExecutionResponse replay(WorkOrderCommandRepository.StoredIdempotency stored, String hash) {
        assertMatchingHash(stored, hash);
        if (stored.responsePayload() == null || stored.statusCode() == null || stored.statusCode() != 200) {
            throw new InvalidCommandException();
        }
        try { return objectMapper.treeToValue(stored.responsePayload(), WorkOrderExecutionResponse.class); }
        catch (JsonProcessingException | IllegalArgumentException exception) { throw new InvalidCommandException(exception); }
    }

    private void assertMatchingHash(WorkOrderCommandRepository.StoredIdempotency stored, String hash) {
        if (stored.requestHash() == null || !MessageDigest.isEqual(
            stored.requestHash().getBytes(StandardCharsets.UTF_8), hash.getBytes(StandardCharsets.UTF_8))) {
            throw new IdempotencyConflictException();
        }
    }

    private WorkOrderEntity createOrder(TenantContext context, ActionProposalEntity proposal,
                                        ProjectEntity project, LocalDateTime now) {
        JsonNode command = proposal.getCommandPayload();
        JsonNode preview = proposal.getAfterSnapshot();
        UUID id = requiredUuid(preview, "id");
        return WorkOrderEntity.builder().id(id).tenantId(context.tenantId())
            .workOrderNo(text(command, "work_order_no", "workOrderNo"))
            .title(text(command, "title")).description(text(command, "description"))
            .projectId(project.getId()).projectName(project.getName())
            .spacePath(text(command, "space_path", "spacePath"))
            .orderType(text(command, "order_type", "orderType"))
            .priority(text(command, "priority")).status(WorkOrderStatus.PENDING_DISPATCH.name())
            .source(text(command, "source")).version(0L).createdBy(context.userId())
            .updatedBy(context.userId()).createdAt(now)
            .dueAt(requiredDateTime(command, "due_at", "dueAt")).build();
    }

    private WorkOrderEntity apply(TenantContext context, WorkOrderEntity current,
                                  ActionProposalEntity proposal, LocalDateTime now) {
        WorkOrderEntity changed = copy(current);
        JsonNode command = proposal.getCommandPayload();
        changed.setUpdatedBy(context.userId());
        switch (proposal.getActionType()) {
            case "UPDATE" -> {
                new WorkOrderStateMachine().assertMutable(status(current));
                setIfText(command, changed::setTitle, "title");
                setIfText(command, changed::setDescription, "description");
                setIfText(command, changed::setPriority, "priority");
                JsonNode due = first(command, "due_at", "dueAt");
                if (due != null && !due.isNull()) changed.setDueAt(LocalDateTime.parse(due.asText()));
            }
            case "ASSIGN" -> {
                transition(changed, WorkOrderAction.ASSIGN, null, now);
                changed.setAssigneeId(requiredUuid(command, "assignee_id", "assigneeId"));
                changed.setAssigneeName(text(command, "assignee_name", "assigneeName"));
            }
            case "ACCEPT" -> transition(changed, WorkOrderAction.ACCEPT, null, now);
            case "START" -> transition(changed, WorkOrderAction.START, null, now);
            case "COMPLETE" -> {
                transition(changed, WorkOrderAction.COMPLETE, null, now);
                changed.setCompletedAt(now);
            }
            case "CLOSE" -> transition(changed, WorkOrderAction.CLOSE, null, now);
            case "CANCEL" -> {
                transition(changed, WorkOrderAction.CANCEL, text(command, "reason"), now);
                changed.setCancelledAt(now);
            }
            default -> throw new InvalidCommandException();
        }
        changed.setVersion(current.getVersion() + 1);
        return changed;
    }

    private void transition(WorkOrderEntity order, WorkOrderAction action, String reason, LocalDateTime now) {
        WorkOrderTransitionResult result = new WorkOrderStateMachine().transition(
            new WorkOrderSnapshot(status(order), order.getAcceptedAt(), reason), action, now);
        order.setStatus(result.status().name());
        order.setAcceptedAt(result.acceptedAt());
        order.setCancelReason(result.cancelReason());
    }

    private JsonNode preview(TenantContext context, WorkOrderEntity current,
                             ActionProposalEntity proposal, LocalDateTime now) {
        try { return snapshot(apply(context, current, proposal, now)); }
        catch (InvalidStateTransitionException | InvalidCommandException exception) { throw exception; }
    }

    private JsonNode freshConflictPreview(TenantContext context, WorkOrderEntity fresh,
                                          ActionProposalEntity proposal, LocalDateTime now) {
        try {
            return preview(context, fresh, proposal, now);
        } catch (InvalidStateTransitionException | InvalidCommandException exception) {
            return snapshot(fresh);
        }
    }

    private AuthorizedTarget loadAuthorizedTarget(TenantContext context, ActionProposalEntity proposal,
                                                  Set<UUID> projects) {
        if ("CREATE".equals(proposal.getActionType())) {
            UUID projectId = requiredUuid(proposal.getCommandPayload(), "project_id", "projectId");
            ProjectEntity project = repository.findProject(context.tenantId(), projectId, projects);
            if (project == null) {
                throw new WorkOrderNotFoundException(
                    text(proposal.getCommandPayload(), "work_order_no", "workOrderNo"));
            }
            return new AuthorizedTarget(project, null);
        }
        WorkOrderEntity current = repository.findWorkOrder(
            context.tenantId(), proposal.getTargetId(), projects);
        if (current == null) throw new WorkOrderNotFoundException("hidden");
        requireSelfAssignment(context, proposal.getActionType(), current);
        return new AuthorizedTarget(null, current);
    }

    private void recheckTargetAccess(TenantContext context, ActionProposalEntity proposal, Set<UUID> projects) {
        if ("CREATE".equals(proposal.getActionType())) {
            UUID projectId = requiredUuid(proposal.getCommandPayload(), "project_id", "projectId");
            if (repository.findProject(context.tenantId(), projectId, projects) == null) {
                throw new WorkOrderNotFoundException(text(proposal.getCommandPayload(), "work_order_no", "workOrderNo"));
            }
            return;
        }
        WorkOrderEntity current = repository.findWorkOrder(context.tenantId(), proposal.getTargetId(), projects);
        if (current == null) throw new WorkOrderNotFoundException("hidden");
        requireSelfAssignment(context, proposal.getActionType(), current);
    }

    private void requireSelfAssignment(TenantContext context, String action, WorkOrderEntity current) {
        if (Set.of("ACCEPT", "START", "COMPLETE").contains(action)
            && !context.userId().equals(current.getAssigneeId())) throw new ActionNotPermittedException();
    }

    private void recoverExpired(TenantContext context, UUID proposalId) {
        try { transactions.required(context, () -> { repository.markProposalExpired(context.tenantId(), proposalId, now()); return null; }); }
        catch (RuntimeException ignored) { }
    }

    private void recoverFailure(TenantContext context, ExecutionAttempt attempt, String code) {
        try { transactions.required(context, () -> {
            if (!repository.markProposalFailed(
                context.tenantId(), attempt.proposalId(), attempt.actorId(), code, now())) {
                throw new IllegalStateException("Proposal failure recovery was not recorded");
            }
            return null;
        }); }
        catch (RuntimeException ignored) { }
    }

    private static void requireOne(int affectedRows) {
        if (affectedRows != 1) throw new InvalidCommandException();
    }

    private String errorCode(RuntimeException exception) {
        if (exception instanceof WorkOrderVersionConflictException) return WorkOrderVersionConflictException.ERROR_CODE;
        if (exception instanceof ActionNotPermittedException) return ActionNotPermittedException.ERROR_CODE;
        if (exception instanceof InvalidStateTransitionException) return InvalidStateTransitionException.ERROR_CODE;
        if (exception instanceof WorkOrderNotFoundException) return "WORK_ORDER_NOT_FOUND";
        if (exception instanceof InvalidCommandException) return InvalidCommandException.ERROR_CODE;
        if (exception instanceof IdempotencyConflictException) return IdempotencyConflictException.ERROR_CODE;
        return "INTERNAL_ERROR";
    }

    public static String requestHash(ObjectMapper mapper, UUID proposalId, JsonNode ignoredCommandPayload) {
        ObjectNode body = mapper.createObjectNode();
        body.put("decision", "CONFIRM");
        body.put("proposal_id", proposalId.toString());
        try {
            byte[] canonical = mapper.writeValueAsBytes(canonical(body, mapper));
            return java.util.HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(canonical));
        } catch (JsonProcessingException | NoSuchAlgorithmException exception) {
            throw new IllegalStateException(exception);
        }
    }

    private static JsonNode canonical(JsonNode node, ObjectMapper mapper) {
        if (node == null || node.isNull() || node.isValueNode()) return node;
        if (node.isArray()) {
            ArrayNode array = mapper.createArrayNode();
            node.forEach(value -> array.add(canonical(value, mapper)));
            return array;
        }
        ObjectNode object = mapper.createObjectNode();
        List<String> names = new ArrayList<>();
        node.fieldNames().forEachRemaining(names::add);
        names.stream().sorted(Comparator.naturalOrder()).forEach(name -> object.set(name, canonical(node.get(name), mapper)));
        return object;
    }

    private ObjectNode snapshot(WorkOrderEntity e) {
        ObjectNode n = objectMapper.createObjectNode();
        put(n,"id",e.getId()); put(n,"tenant_id",e.getTenantId()); put(n,"work_order_no",e.getWorkOrderNo());
        put(n,"title",e.getTitle()); put(n,"description",e.getDescription()); put(n,"project_id",e.getProjectId());
        put(n,"project_name",e.getProjectName()); put(n,"space_path",e.getSpacePath()); put(n,"order_type",e.getOrderType());
        put(n,"priority",e.getPriority()); put(n,"status",e.getStatus()); put(n,"assignee_id",e.getAssigneeId());
        put(n,"assignee_name",e.getAssigneeName()); put(n,"source",e.getSource()); n.put("version",e.getVersion());
        put(n,"root_work_order_id",e.getRootWorkOrderId()); put(n,"root_work_order_no",e.getRootWorkOrderNo());
        put(n,"rework_reason",e.getReworkReason());
        put(n,"accepted_at",e.getAcceptedAt()); put(n,"created_by",e.getCreatedBy()); put(n,"updated_by",e.getUpdatedBy());
        put(n,"created_at",e.getCreatedAt()); put(n,"due_at",e.getDueAt()); put(n,"completed_at",e.getCompletedAt());
        put(n,"cancelled_at",e.getCancelledAt()); put(n,"cancel_reason",e.getCancelReason());
        return n;
    }

    private static WorkOrderEntity copy(WorkOrderEntity e) {
        return WorkOrderEntity.builder().id(e.getId()).tenantId(e.getTenantId()).workOrderNo(e.getWorkOrderNo())
            .title(e.getTitle()).description(e.getDescription()).projectId(e.getProjectId()).projectName(e.getProjectName())
            .spacePath(e.getSpacePath()).orderType(e.getOrderType()).priority(e.getPriority()).status(e.getStatus())
            .assigneeId(e.getAssigneeId()).assigneeName(e.getAssigneeName()).source(e.getSource())
            .rootWorkOrderId(e.getRootWorkOrderId()).rootWorkOrderNo(e.getRootWorkOrderNo()).reworkReason(e.getReworkReason())
            .version(e.getVersion()).acceptedAt(e.getAcceptedAt()).createdBy(e.getCreatedBy()).updatedBy(e.getUpdatedBy())
            .createdAt(e.getCreatedAt()).dueAt(e.getDueAt()).completedAt(e.getCompletedAt())
            .cancelledAt(e.getCancelledAt()).cancelReason(e.getCancelReason()).build();
    }

    private static WorkOrderStatus status(WorkOrderEntity e) {
        try { return WorkOrderStatus.valueOf(e.getStatus()); }
        catch (RuntimeException exception) { throw new InvalidCommandException(exception); }
    }
    private static JsonNode first(JsonNode n, String... names) { for (String name:names) if (n.has(name)) return n.get(name); return null; }
    private static String text(JsonNode n, String... names) { JsonNode v=first(n,names); if(v==null||!v.isTextual()||v.asText().isBlank()) throw new InvalidCommandException(); return v.asText().strip(); }
    private static UUID requiredUuid(JsonNode n, String... names) { try { return UUID.fromString(text(n,names)); } catch(RuntimeException e){ if(e instanceof InvalidCommandException i) throw i; throw new InvalidCommandException(e);} }
    private static LocalDateTime requiredDateTime(JsonNode n, String... names) { try { return LocalDateTime.parse(text(n,names)); } catch(RuntimeException e){ if(e instanceof InvalidCommandException i) throw i; throw new InvalidCommandException(e);} }
    private static void setIfText(JsonNode n, java.util.function.Consumer<String> setter, String... names) { JsonNode v=first(n,names); if(v!=null&&!v.isNull()) setter.accept(text(n,names)); }
    private static void put(ObjectNode n,String k,Object v){ if(v instanceof UUID u)n.put(k,u.toString()); else if(v instanceof String s)n.put(k,s); else if(v instanceof LocalDateTime d)n.put(k,d.toString()); }
    private LocalDateTime now() { return LocalDateTime.ofInstant(clock.instant(), ZoneOffset.UTC); }
    private record CurrentAuthority(Set<String> roles, Set<UUID> projects) { }
    private record AuthorizedTarget(ProjectEntity project, WorkOrderEntity workOrder) { }

    private static final class ExecutionAttempt {
        private final UUID proposalId;
        private UUID actorId;
        private boolean authorized;
        private boolean claimed;

        private ExecutionAttempt(UUID proposalId) { this.proposalId = proposalId; }
        private void authorized(UUID actorId) { this.actorId = actorId; this.authorized = true; }
        private void claimed() { this.claimed = true; }
        private boolean recoverable() { return authorized && claimed && actorId != null; }
        private UUID proposalId() { return proposalId; }
        private UUID actorId() { return actorId; }
    }
}
