package com.tangmeng.workorder.command;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.tangmeng.workorder.domain.ActionProposalEntity;
import com.tangmeng.workorder.domain.ProjectEntity;
import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.domain.WorkOrderEventEntity;
import com.tangmeng.workorder.mapper.ActionProposalMapper;
import com.tangmeng.workorder.mapper.ProjectMapper;
import com.tangmeng.workorder.mapper.WorkOrderEventMapper;
import com.tangmeng.workorder.mapper.WorkOrderMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

@Repository
@RequiredArgsConstructor
public class JdbcWorkOrderCommandRepository implements WorkOrderCommandRepository {

    private final JdbcTemplate jdbc;
    private final ActionProposalMapper proposalMapper;
    private final ProjectMapper projectMapper;
    private final WorkOrderMapper workOrderMapper;
    private final WorkOrderEventMapper eventMapper;
    private final ObjectMapper objectMapper;

    @Override
    public Optional<StoredIdempotency> findIdempotency(UUID tenantId, String operation, String key) {
        List<StoredIdempotency> rows = jdbc.query("""
            select request_hash, response_payload::text, status_code
            from idempotency_record
            where tenant_id = ? and operation = ? and idempotency_key = ?
            """, (rs, row) -> new StoredIdempotency(
                rs.getString("request_hash"), readJson(rs.getString("response_payload")),
                rs.getObject("status_code", Integer.class)), tenantId, operation, key);
        return rows.stream().findFirst();
    }

    @Override
    public boolean reserveIdempotency(UUID tenantId, String operation, String key,
                                      String requestHash, LocalDateTime now) {
        return jdbc.update("""
            insert into idempotency_record
              (id,tenant_id,operation,idempotency_key,request_hash,created_at,expires_at)
            values (?,?,?,?,?,?,?)
            on conflict (tenant_id,operation,idempotency_key) do nothing
            """, UUID.randomUUID(), tenantId, operation, key, requestHash, now, now.plusDays(1)) == 1;
    }

    @Override
    public ActionProposalEntity findProposal(UUID tenantId, UUID proposalId) {
        return proposalMapper.selectProposalById(tenantId, proposalId);
    }

    @Override
    public boolean claimProposal(UUID tenantId, UUID proposalId, UUID actorId, LocalDateTime now) {
        return jdbc.update("""
            update action_proposal
            set status = 'EXECUTING', confirmed_by = ?, updated_at = ?
            where tenant_id = ? and id = ?
              and expires_at > ?
              and (status = 'PENDING_CONFIRMATION'
                   or (status = 'CONFIRMED' and confirmed_by = ?))
            """, actorId, now, tenantId, proposalId, now, actorId) == 1;
    }

    @Override
    public ProjectEntity findProject(UUID tenantId, UUID projectId, Set<UUID> authorizedProjects) {
        if (authorizedProjects.isEmpty() || !authorizedProjects.contains(projectId)) return null;
        return projectMapper.selectOne(Wrappers.<ProjectEntity>lambdaQuery()
            .eq(ProjectEntity::getTenantId, tenantId).eq(ProjectEntity::getId, projectId)
            .eq(ProjectEntity::getStatus, "ACTIVE"));
    }

    @Override
    public WorkOrderEntity findWorkOrder(UUID tenantId, UUID workOrderId, Set<UUID> authorizedProjects) {
        if (authorizedProjects.isEmpty()) return null;
        return workOrderMapper.selectOne(Wrappers.<WorkOrderEntity>lambdaQuery()
            .eq(WorkOrderEntity::getTenantId, tenantId).eq(WorkOrderEntity::getId, workOrderId)
            .in(WorkOrderEntity::getProjectId, authorizedProjects));
    }

    @Override
    public WorkOrderEntity findWorkOrderByIdentity(UUID tenantId, UUID workOrderId, String workOrderNo,
                                                   Set<UUID> authorizedProjects) {
        if (authorizedProjects.isEmpty()) return null;
        return workOrderMapper.selectOne(Wrappers.<WorkOrderEntity>lambdaQuery()
            .eq(WorkOrderEntity::getTenantId, tenantId)
            .in(WorkOrderEntity::getProjectId, authorizedProjects)
            .and(identity -> identity.eq(WorkOrderEntity::getId, workOrderId)
                .or().eq(WorkOrderEntity::getWorkOrderNo, workOrderNo)));
    }

    @Override
    public InsertWorkOrderResult insertWorkOrder(WorkOrderEntity order) {
        try {
            int inserted = jdbc.update("""
                insert into work_order
                  (id,tenant_id,work_order_no,title,description,project_id,project_name,space_path,
                   order_type,priority,status,assignee_id,assignee_name,source,root_work_order_id,
                   root_work_order_no,rework_reason,version,accepted_at,created_by,updated_by,created_at,
                   due_at,completed_at,cancelled_at,cancel_reason)
                values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                on conflict do nothing
                """, order.getId(), order.getTenantId(), order.getWorkOrderNo(), order.getTitle(),
                order.getDescription(), order.getProjectId(), order.getProjectName(), order.getSpacePath(),
                order.getOrderType(), order.getPriority(), order.getStatus(), order.getAssigneeId(),
                order.getAssigneeName(), order.getSource(), order.getRootWorkOrderId(),
                order.getRootWorkOrderNo(), order.getReworkReason(), order.getVersion(), order.getAcceptedAt(),
                order.getCreatedBy(), order.getUpdatedBy(), order.getCreatedAt(), order.getDueAt(),
                order.getCompletedAt(), order.getCancelledAt(), order.getCancelReason());
            return inserted == 1 ? InsertWorkOrderResult.INSERTED : InsertWorkOrderResult.DUPLICATE;
        } catch (DataIntegrityViolationException exception) {
            throw new InvalidCommandException(exception);
        }
    }

    @Override
    public boolean updateWorkOrder(WorkOrderEntity order, long expectedVersion) {
        return jdbc.update("""
            update work_order set title=?, description=?, priority=?, status=?, assignee_id=?,
              assignee_name=?, version=?, accepted_at=?, updated_by=?, due_at=?, completed_at=?,
              cancelled_at=?, cancel_reason=?
            where tenant_id=? and id=? and project_id=? and version=?
            """, order.getTitle(), order.getDescription(), order.getPriority(), order.getStatus(),
            order.getAssigneeId(), order.getAssigneeName(), order.getVersion(), order.getAcceptedAt(),
            order.getUpdatedBy(), order.getDueAt(), order.getCompletedAt(), order.getCancelledAt(),
            order.getCancelReason(), order.getTenantId(), order.getId(), order.getProjectId(), expectedVersion) == 1;
    }

    @Override
    public int closeOpenAssignment(UUID tenantId, UUID workOrderId, LocalDateTime now) {
        return jdbc.update("""
            update work_order_assignment set unassigned_at = ?
            where tenant_id = ? and work_order_id = ? and unassigned_at is null
            """, now, tenantId, workOrderId);
    }

    @Override
    public int insertAssignment(UUID tenantId, UUID workOrderId, UUID assigneeId,
                                 String reason, UUID actorId, LocalDateTime now) {
        return jdbc.update("""
            insert into work_order_assignment
              (id,tenant_id,work_order_id,assignee_id,assigned_at,reason,created_by,created_at)
            values (?,?,?,?,?,?,?,?)
            """, UUID.randomUUID(), tenantId, workOrderId, assigneeId, now, reason, actorId, now);
    }

    @Override
    public int insertEvent(WorkOrderEventEntity event) {
        return eventMapper.insert(event);
    }

    @Override
    public int insertOutbox(UUID tenantId, UUID aggregateId, String eventType,
                             JsonNode payload, LocalDateTime now) {
        return jdbc.update("""
            insert into outbox_event
              (id,tenant_id,aggregate_id,aggregate_type,event_type,payload,status,occurred_at,available_at)
            values (?,?,?,'WORK_ORDER',?,?::jsonb,'PENDING',?,?)
            """, UUID.randomUUID(), tenantId, aggregateId, eventType, writeJson(payload), now, now);
    }

    @Override
    public boolean completeIdempotency(UUID tenantId, String operation, String key, String requestHash,
                                       JsonNode response, int statusCode) {
        return jdbc.update("""
            update idempotency_record set response_payload=?::jsonb,status_code=?
            where tenant_id=? and operation=? and idempotency_key=? and request_hash=?
              and response_payload is null and status_code is null
            """, writeJson(response), statusCode, tenantId, operation, key, requestHash) == 1;
    }

    @Override
    public boolean markProposalExecuted(UUID tenantId, UUID proposalId, JsonNode response, LocalDateTime now) {
        return jdbc.update("""
            update action_proposal set status='EXECUTED', execution_result=?::jsonb,
              error_code=null, updated_at=?
            where tenant_id=? and id=? and status='EXECUTING'
            """, writeJson(response), now, tenantId, proposalId) == 1;
    }

    @Override
    public boolean markProposalFailed(UUID tenantId, UUID proposalId, UUID actorId,
                                      String errorCode, LocalDateTime now) {
        return jdbc.update("""
            update action_proposal set status='FAILED', confirmed_by=?, error_code=?, updated_at=?
            where tenant_id=? and id=? and status in ('PENDING_CONFIRMATION','CONFIRMED','EXECUTING')
            """, actorId, errorCode, now, tenantId, proposalId) == 1;
    }

    @Override
    public boolean rejectProposal(UUID tenantId, UUID proposalId, UUID actorId, LocalDateTime now) {
        return jdbc.update("""
            update action_proposal set status='REJECTED', confirmed_by=?, updated_at=?
            where tenant_id=? and id=? and status='PENDING_CONFIRMATION' and expires_at>?
            """, actorId, now, tenantId, proposalId, now) == 1;
    }

    @Override
    public boolean markProposalExpired(UUID tenantId, UUID proposalId, LocalDateTime now) {
        return jdbc.update("""
            update action_proposal set status='EXPIRED', error_code='ACTION_PROPOSAL_EXPIRED', updated_at=?
            where tenant_id=? and id=? and status in ('PENDING_CONFIRMATION','CONFIRMED') and expires_at<=?
            """, now, tenantId, proposalId, now) == 1;
    }

    @Override
    public boolean markRectificationStarted(UUID tenantId, UUID proposalId, UUID workOrderId,
                                            UUID actorId, LocalDateTime now) {
        return jdbc.update("""
            update rectification_case
            set rectification_work_order_id=?, status='RECTIFYING', updated_by=?, updated_at=?
            where tenant_id=? and proposal_id=? and status='PROPOSED'
              and current_verdict in ('FAIL','UNCERTAIN')
            """, workOrderId, actorId, now, tenantId, proposalId) == 1;
    }

    @Override
    public int markRectificationClosed(UUID tenantId, UUID proposalId, UUID actorId,
                                       LocalDateTime now) {
        return jdbc.update("""
            update rectification_case
            set status='CLOSED', closed_at=?, updated_by=?, updated_at=?
            where tenant_id=? and proposal_id=? and status='PROPOSED'
              and current_verdict='PASS'
            """, now, actorId, now, tenantId, proposalId);
    }

    private JsonNode readJson(String value) {
        if (value == null) return null;
        try { return objectMapper.readTree(value); }
        catch (JsonProcessingException exception) { throw new IllegalStateException(exception); }
    }

    private String writeJson(JsonNode value) {
        try { return objectMapper.writeValueAsString(value); }
        catch (JsonProcessingException exception) { throw new IllegalStateException(exception); }
    }
}
