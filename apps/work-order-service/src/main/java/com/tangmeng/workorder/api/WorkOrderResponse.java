package com.tangmeng.workorder.api;

import com.tangmeng.workorder.domain.WorkOrderEntity;

import java.time.LocalDateTime;

public record WorkOrderResponse(
    String workOrderNo,
    String title,
    String description,
    String projectName,
    String spacePath,
    String orderType,
    String priority,
    String status,
    String assigneeName,
    String source,
    String rootWorkOrderNo,
    String reworkReason,
    LocalDateTime createdAt,
    LocalDateTime dueAt,
    LocalDateTime completedAt
) {
    public static WorkOrderResponse from(WorkOrderEntity entity) {
        return new WorkOrderResponse(
            entity.getWorkOrderNo(),
            entity.getTitle(),
            entity.getDescription(),
            entity.getProjectName(),
            entity.getSpacePath(),
            entity.getOrderType(),
            entity.getPriority(),
            entity.getStatus(),
            entity.getAssigneeName(),
            entity.getSource(),
            entity.getRootWorkOrderNo(),
            entity.getReworkReason(),
            entity.getCreatedAt(),
            entity.getDueAt(),
            entity.getCompletedAt()
        );
    }
}
