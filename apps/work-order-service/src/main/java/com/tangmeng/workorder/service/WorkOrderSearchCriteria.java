package com.tangmeng.workorder.service;

import java.time.LocalDateTime;

public record WorkOrderSearchCriteria(
    String status,
    String priority,
    String projectName,
    String assigneeName,
    LocalDateTime createdFrom,
    LocalDateTime createdTo
) {
}

