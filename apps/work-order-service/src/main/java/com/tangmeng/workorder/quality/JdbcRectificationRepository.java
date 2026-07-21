package com.tangmeng.workorder.quality;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

@Repository
@RequiredArgsConstructor
public class JdbcRectificationRepository implements RectificationRepository {
    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;

    @Override
    public boolean lockWorkOrder(UUID tenantId, UUID workOrderId) {
        return !jdbc.query("""
            select id from work_order
            where tenant_id=? and id=?
            for update
            """, (rs, rowNum) -> rs.getObject("id", UUID.class), tenantId, workOrderId).isEmpty();
    }

    @Override
    public RectificationCaseEntity findByResult(UUID tenantId, UUID resultId) {
        return one(jdbc.query(select() + " where tenant_id=? and current_quality_result_id=?",
            mapper(), tenantId, resultId));
    }

    @Override
    public RectificationCaseEntity findByOriginalRound(UUID tenantId, UUID workOrderId, int round) {
        return one(jdbc.query(select()
                + " where tenant_id=? and original_work_order_id=? and inspection_round=?",
            mapper(), tenantId, workOrderId, round));
    }

    @Override
    public boolean insertCase(RectificationCaseEntity entity) {
        return jdbc.update("""
            insert into rectification_case
              (id,tenant_id,original_work_order_id,current_quality_result_id,current_verdict,
               proposal_id,rectification_work_order_id,inspection_round,status,created_by,
               updated_by,created_at,updated_at,closed_at)
            values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, entity.getId(), entity.getTenantId(), entity.getOriginalWorkOrderId(),
            entity.getCurrentQualityResultId(), entity.getCurrentVerdict(), entity.getProposalId(),
            entity.getRectificationWorkOrderId(), entity.getInspectionRound(), entity.getStatus(),
            entity.getCreatedBy(), entity.getUpdatedBy(), entity.getCreatedAt(), entity.getUpdatedAt(),
            entity.getClosedAt()) == 1;
    }

    @Override
    public boolean insertReviewEvent(UUID tenantId, UUID caseId, UUID resultId, String decision,
                                     String verdict, String reason, JsonNode payload, UUID actorId,
                                     LocalDateTime now) {
        return jdbc.update("""
            insert into quality_review_event
              (id,tenant_id,rectification_case_id,quality_result_id,decision,previous_verdict,
               reviewed_verdict,reason,review_payload,actor_id,created_at)
            values (?,?,?,?,?,?,?,?,?::jsonb,?,?)
            """, UUID.randomUUID(), tenantId, caseId, resultId, decision, verdict, verdict,
            reason, json(payload), actorId, now) == 1;
    }

    private static String select() {
        return """
            select id,tenant_id,original_work_order_id,current_quality_result_id,current_verdict,
                   proposal_id,rectification_work_order_id,inspection_round,status,created_by,
                   updated_by,created_at,updated_at,closed_at
            from rectification_case
            """;
    }

    private static org.springframework.jdbc.core.RowMapper<RectificationCaseEntity> mapper() {
        return (rs, rowNum) -> RectificationCaseEntity.builder()
            .id(rs.getObject("id", UUID.class))
            .tenantId(rs.getObject("tenant_id", UUID.class))
            .originalWorkOrderId(rs.getObject("original_work_order_id", UUID.class))
            .currentQualityResultId(rs.getObject("current_quality_result_id", UUID.class))
            .currentVerdict(rs.getString("current_verdict"))
            .proposalId(rs.getObject("proposal_id", UUID.class))
            .rectificationWorkOrderId(rs.getObject("rectification_work_order_id", UUID.class))
            .inspectionRound(rs.getInt("inspection_round"))
            .status(rs.getString("status"))
            .createdBy(rs.getObject("created_by", UUID.class))
            .updatedBy(rs.getObject("updated_by", UUID.class))
            .createdAt(local(rs.getTimestamp("created_at")))
            .updatedAt(local(rs.getTimestamp("updated_at")))
            .closedAt(local(rs.getTimestamp("closed_at")))
            .build();
    }

    private String json(JsonNode node) {
        try {
            return objectMapper.writeValueAsString(node);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException(exception);
        }
    }

    private static RectificationCaseEntity one(List<RectificationCaseEntity> rows) {
        if (rows.size() > 1) {
            throw new IllegalStateException("Rectification case uniqueness was violated");
        }
        return rows.isEmpty() ? null : rows.get(0);
    }

    private static LocalDateTime local(Timestamp timestamp) {
        return timestamp == null ? null : timestamp.toLocalDateTime();
    }
}
