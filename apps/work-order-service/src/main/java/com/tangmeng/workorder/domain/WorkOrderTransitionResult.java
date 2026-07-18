package com.tangmeng.workorder.domain;

import java.time.LocalDateTime;

public record WorkOrderTransitionResult(
    WorkOrderStatus status,
    LocalDateTime acceptedAt,
    String cancelReason
) {
}
