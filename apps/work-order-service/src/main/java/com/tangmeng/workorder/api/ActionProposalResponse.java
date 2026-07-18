package com.tangmeng.workorder.api;

import com.fasterxml.jackson.databind.JsonNode;

import java.time.LocalDateTime;
import java.util.UUID;

public record ActionProposalResponse(
    UUID id,
    String actionType,
    String targetWorkOrderNo,
    String riskLevel,
    String status,
    JsonNode beforeSnapshot,
    JsonNode afterSnapshot,
    long expectedVersion,
    LocalDateTime expiresAt
) {
}
