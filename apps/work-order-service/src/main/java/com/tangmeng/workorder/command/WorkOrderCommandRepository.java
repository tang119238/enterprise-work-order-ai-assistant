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

    record StoredIdempotency(String requestHash, JsonNode responsePayload, int statusCode) { }

    Optional<StoredIdempotency> findIdempotency(UUID tenantId, String operation, String key);
    ActionProposalEntity findProposal(UUID tenantId, UUID proposalId);
    boolean claimProposal(UUID tenantId, UUID proposalId, UUID actorId, LocalDateTime now);
    ProjectEntity findProject(UUID tenantId, UUID projectId, Set<UUID> authorizedProjects);
    WorkOrderEntity findWorkOrder(UUID tenantId, UUID workOrderId, Set<UUID> authorizedProjects);
    boolean insertWorkOrder(WorkOrderEntity order);
    boolean updateWorkOrder(WorkOrderEntity order, long expectedVersion);
    void closeOpenAssignment(UUID tenantId, UUID workOrderId, LocalDateTime now);
    void insertAssignment(UUID tenantId, UUID workOrderId, UUID assigneeId,
                          String reason, UUID actorId, LocalDateTime now);
    void insertEvent(WorkOrderEventEntity event);
    void insertOutbox(UUID tenantId, UUID aggregateId, String eventType,
                      JsonNode payload, LocalDateTime now);
    void saveIdempotency(UUID tenantId, String operation, String key, String requestHash,
                         JsonNode response, int statusCode, LocalDateTime now);
    boolean markProposalExecuted(UUID tenantId, UUID proposalId, JsonNode response, LocalDateTime now);
    void markProposalFailed(UUID tenantId, UUID proposalId, String errorCode, LocalDateTime now);
    boolean rejectProposal(UUID tenantId, UUID proposalId, UUID actorId, LocalDateTime now);
    void markProposalExpired(UUID tenantId, UUID proposalId, LocalDateTime now);
}
