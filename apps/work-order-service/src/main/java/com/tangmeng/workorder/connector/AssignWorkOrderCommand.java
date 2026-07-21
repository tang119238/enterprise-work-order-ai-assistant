package com.tangmeng.workorder.connector;

import java.util.UUID;

/**
 * Command to assign a work order.
 */
public record AssignWorkOrderCommand(
    UUID assigneeId,
    String assigneeName,
    String reason
) {}
