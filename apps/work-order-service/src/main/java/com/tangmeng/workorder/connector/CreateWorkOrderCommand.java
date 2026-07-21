package com.tangmeng.workorder.connector;

import java.util.UUID;

/**
 * Command to create a new work order.
 */
public record CreateWorkOrderCommand(
    String title,
    String description,
    UUID projectId,
    String projectName,
    String orderType,
    String priority,
    String source
) {}
