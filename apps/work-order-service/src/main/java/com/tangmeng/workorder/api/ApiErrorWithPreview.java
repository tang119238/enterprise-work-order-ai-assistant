package com.tangmeng.workorder.api;

import com.fasterxml.jackson.databind.JsonNode;

import java.time.Instant;

public record ApiErrorWithPreview(String code, String message, JsonNode freshPreview, Instant timestamp) {
    public static ApiErrorWithPreview versionConflict(JsonNode preview) {
        return new ApiErrorWithPreview("WORK_ORDER_VERSION_CONFLICT",
            "Work order version conflict", preview, Instant.now());
    }
}
