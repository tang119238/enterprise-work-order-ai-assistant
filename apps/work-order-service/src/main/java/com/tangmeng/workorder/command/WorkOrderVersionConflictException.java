package com.tangmeng.workorder.command;

import com.fasterxml.jackson.databind.JsonNode;

public class WorkOrderVersionConflictException extends RuntimeException {
    public static final String ERROR_CODE = "WORK_ORDER_VERSION_CONFLICT";
    private final JsonNode freshPreview;

    public WorkOrderVersionConflictException(JsonNode freshPreview) {
        super(ERROR_CODE);
        this.freshPreview = freshPreview;
    }

    public JsonNode getFreshPreview() {
        return freshPreview;
    }
}
