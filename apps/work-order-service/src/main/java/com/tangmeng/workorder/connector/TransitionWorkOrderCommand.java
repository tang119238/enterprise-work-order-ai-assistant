package com.tangmeng.workorder.connector;

import java.util.UUID;

/**
 * Command to transition work order status.
 */
public record TransitionWorkOrderCommand(
    String targetStatus,
    String reason,
    UUID actorId
) {}
