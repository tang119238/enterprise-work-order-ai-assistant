package com.tangmeng.workorder.quality;

import java.util.UUID;

public record QualityResultCallbackResponse(
    UUID resultId,
    UUID rectificationCaseId,
    UUID proposalId,
    String actionType,
    String status
) {
}
