package com.tangmeng.workorder.connector;

import java.time.Instant;
import java.util.UUID;

/**
 * Stable work order DTO returned by connectors.
 */
public record ConnectorWorkOrder(
    UUID id,
    String workOrderNo,
    String title,
    String description,
    String projectName,
    String orderType,
    String priority,
    String status,
    String source,
    String assigneeName,
    UUID assigneeId,
    String rootWorkOrderNo,
    UUID rootWorkOrderId,
    long version,
    Instant createdAt,
    Instant dueAt,
    Instant acceptedAt,
    Instant completedAt,
    Instant cancelledAt
) {}
