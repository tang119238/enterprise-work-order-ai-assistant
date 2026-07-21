package com.tangmeng.workorder.quality;

import com.fasterxml.jackson.databind.JsonNode;

import java.time.LocalDateTime;
import java.util.UUID;

public interface RectificationRepository {
    boolean lockWorkOrder(UUID tenantId, UUID workOrderId);
    RectificationCaseEntity findByResult(UUID tenantId, UUID resultId);
    RectificationCaseEntity findByOriginalRound(UUID tenantId, UUID workOrderId, int round);
    boolean insertCase(RectificationCaseEntity entity);
    boolean insertReviewEvent(UUID tenantId, UUID caseId, UUID resultId, String decision,
                              String verdict, String reason, JsonNode payload, UUID actorId,
                              LocalDateTime now);
}
