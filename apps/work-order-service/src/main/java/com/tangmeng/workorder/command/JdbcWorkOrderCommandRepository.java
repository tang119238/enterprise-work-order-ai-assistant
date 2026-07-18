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
                rs.getInt("status_code")), tenantId, operation, key);
        return rows.stream().findFirst();
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
    public boolean insertWorkOrder(WorkOrderEntity order) {
        try {
            return workOrderMapper.insert(order) == 1;
        } catch (DataIntegrityViolationException exception) {
            return false;
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
    public void closeOpenAssignment(UUID tenantId, UUID workOrderId, LocalDateTime now) {
        jdbc.update("""
            update work_order_assignment set unassigned_at = ?
            where tenant_id = ? and work_order_id = ? and unassigned_at is null
            """, now, tenantId, workOrderId);
    }

    @Override
    public void insertAssignment(UUID tenantId, UUID workOrderId, UUID assigneeId,
                                 String reason, UUID actorId, LocalDateTime now) {
        jdbc.update("""
            insert into work_order_assignment
              (id,tenant_id,work_order_id,assignee_id,assigned_at,reason,created_by,created_at)
            values (?,?,?,?,?,?,?,?)
            """, UUID.randomUUID(), tenantId, workOrderId, assigneeId, now, reason, actorId, now);
    }

    @Override
    public void insertEvent(WorkOrderEventEntity event) {
        if (eventMapper.insert(event) != 1) throw new IllegalStateException("Event insert failed");
    }

    @Override
    public void insertOutbox(UUID tenantId, UUID aggregateId, String eventType,
                             JsonNode payload, LocalDateTime now) {
        jdbc.update("""
            insert into outbox_event
              (id,tenant_id,aggregate_id,aggregate_type,event_type,payload,status,occurred_at,available_at)
            values (?,?,?,'WORK_ORDER',?,?::jsonb,'PENDING',?,?)
            """, UUID.randomUUID(), tenantId, aggregateId, eventType, writeJson(payload), now, now);
    }

    @Override
    public void saveIdempotency(UUID tenantId, String operation, String key, String requestHash,
                                JsonNode response, int statusCode, LocalDateTime now) {
        jdbc.update("""
            insert into idempotency_record
              (id,tenant_id,operation,idempotency_key,request_hash,response_payload,status_code,created_at,expires_at)
            values (?,?,?,?,?,?::jsonb,?,?,?)
            """, UUID.randomUUID(), tenantId, operation, key, requestHash, writeJson(response),
            statusCode, now, now.plusDays(1));
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
    public void markProposalFailed(UUID tenantId, UUID proposalId, String errorCode, LocalDateTime now) {
        jdbc.update("""
            update action_proposal set status='FAILED', error_code=?, updated_at=?
            where tenant_id=? and id=? and status in ('PENDING_CONFIRMATION','CONFIRMED','EXECUTING')
            """, errorCode, now, tenantId, proposalId);
    }

    @Override
    public boolean rejectProposal(UUID tenantId, UUID proposalId, UUID actorId, LocalDateTime now) {
        return jdbc.update("""
            update action_proposal set status='REJECTED', confirmed_by=?, updated_at=?
            where tenant_id=? and id=? and status='PENDING_CONFIRMATION' and expires_at>?
            """, actorId, now, tenantId, proposalId, now) == 1;
    }

    @Override
    public void markProposalExpired(UUID tenantId, UUID proposalId, LocalDateTime now) {
        jdbc.update("""
            update action_proposal set status='EXPIRED', error_code='ACTION_PROPOSAL_EXPIRED', updated_at=?
            where tenant_id=? and id=? and status='PENDING_CONFIRMATION' and expires_at<=?
            """, now, tenantId, proposalId, now);
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
