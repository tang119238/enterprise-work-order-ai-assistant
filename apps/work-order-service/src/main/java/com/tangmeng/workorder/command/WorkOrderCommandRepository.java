package com.tangmeng.workorder.command;

import com.fasterxml.jackson.databind.JsonNode;
import com.tangmeng.workorder.domain.ActionProposalEntity;
import com.tangmeng.workorder.domain.ProjectEntity;
import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.domain.WorkOrderEventEntity;

import java.time.LocalDateTime;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

public interface WorkOrderCommandRepository {

    record StoredIdempotency(String requestHash, JsonNode responsePayload, Integer statusCode) { }
    enum InsertWorkOrderResult { INSERTED, DUPLICATE, INVALID }

    Optional<StoredIdempotency> findIdempotency(UUID tenantId, String operation, String key);
    boolean reserveIdempotency(UUID tenantId, String operation, String key, String requestHash,
                               LocalDateTime now);
    ActionProposalEntity findProposal(UUID tenantId, UUID proposalId);
    boolean claimProposal(UUID tenantId, UUID proposalId, UUID actorId, LocalDateTime now);
    ProjectEntity findProject(UUID tenantId, UUID projectId, Set<UUID> authorizedProjects);
    WorkOrderEntity findWorkOrder(UUID tenantId, UUID workOrderId, Set<UUID> authorizedProjects);
    WorkOrderEntity findWorkOrderByIdentity(UUID tenantId, UUID workOrderId, String workOrderNo,
                                            Set<UUID> authorizedProjects);
    InsertWorkOrderResult insertWorkOrder(WorkOrderEntity order);
    boolean updateWorkOrder(WorkOrderEntity order, long expectedVersion);
    int closeOpenAssignment(UUID tenantId, UUID workOrderId, LocalDateTime now);
    int insertAssignment(UUID tenantId, UUID workOrderId, UUID assigneeId,
                          String reason, UUID actorId, LocalDateTime now);
    int insertEvent(WorkOrderEventEntity event);
    int insertOutbox(UUID tenantId, UUID aggregateId, String eventType,
                      JsonNode payload, LocalDateTime now);
    boolean completeIdempotency(UUID tenantId, String operation, String key, String requestHash,
                                JsonNode response, int statusCode);
    boolean markProposalExecuted(UUID tenantId, UUID proposalId, JsonNode response, LocalDateTime now);
    boolean markProposalFailed(UUID tenantId, UUID proposalId, UUID actorId,
                               String errorCode, LocalDateTime now);
    boolean rejectProposal(UUID tenantId, UUID proposalId, UUID actorId, LocalDateTime now);
    boolean markProposalExpired(UUID tenantId, UUID proposalId, LocalDateTime now);
    boolean markRectificationStarted(UUID tenantId, UUID proposalId, UUID workOrderId,
                                     UUID actorId, LocalDateTime now);
    int markRectificationClosed(UUID tenantId, UUID proposalId, UUID actorId,
                                LocalDateTime now);
}
