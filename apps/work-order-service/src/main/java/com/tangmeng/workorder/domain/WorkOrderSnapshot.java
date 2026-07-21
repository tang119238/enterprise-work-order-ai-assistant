package com.tangmeng.workorder.domain;

import java.time.LocalDateTime;

public record WorkOrderSnapshot(
    WorkOrderStatus status,
    LocalDateTime acceptedAt,
    String cancelReason
) {
}
