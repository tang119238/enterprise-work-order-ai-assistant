package com.tangmeng.workorder.api;

import java.util.UUID;

public record WorkOrderExecutionResponse(
    UUID proposalId,
    UUID workOrderId,
    String workOrderNo,
    String actionType,
    String status,
    long version
) {
}
