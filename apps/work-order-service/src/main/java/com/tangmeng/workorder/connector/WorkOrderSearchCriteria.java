package com.tangmeng.workorder.connector;

import java.util.List;
import java.util.UUID;

/**
 * Search criteria for work order queries.
 */
public record WorkOrderSearchCriteria(
    String status,
    String priority,
    String projectName,
    String assigneeName,
    String orderType,
    List<UUID> projectIds
) {}
