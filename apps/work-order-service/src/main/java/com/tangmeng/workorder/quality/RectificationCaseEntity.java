package com.tangmeng.workorder.quality;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;
import java.util.UUID;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class RectificationCaseEntity {
    private UUID id;
    private UUID tenantId;
    private UUID originalWorkOrderId;
    private UUID currentQualityResultId;
    private String currentVerdict;
    private UUID proposalId;
    private UUID rectificationWorkOrderId;
    private int inspectionRound;
    private String status;
    private UUID createdBy;
    private UUID updatedBy;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
    private LocalDateTime closedAt;
}
