package com.tangmeng.workorder.connector;

/**
 * Command to update work order fields.
 */
public record UpdateWorkOrderCommand(
    String title,
    String description,
    String priority
) {}
