package com.tangmeng.workorder.quality;

import com.fasterxml.jackson.databind.JsonNode;
import com.tangmeng.workorder.command.InvalidCommandException;

import java.util.Set;
import java.util.UUID;

public record QualityResultCallback(
    UUID resultId,
    UUID qualityJobId,
    UUID tenantId,
    UUID workOrderId,
    long workOrderVersion,
    int inspectionRound,
    String verdict,
    double confidence,
    JsonNode workOrderSnapshot,
    JsonNode policyVersions,
    JsonNode findings,
    JsonNode provenance
) {
    private static final Set<String> VERDICTS = Set.of("PASS", "FAIL", "UNCERTAIN", "SKIP");

    public QualityResultCallback requireValid() {
        if (resultId == null || qualityJobId == null || tenantId == null || workOrderId == null
            || workOrderVersion < 0 || inspectionRound < 1 || !VERDICTS.contains(verdict)
            || !Double.isFinite(confidence) || confidence < 0 || confidence > 1
            || workOrderSnapshot == null || !workOrderSnapshot.isObject()
            || policyVersions == null || !policyVersions.isObject()
            || findings == null || !findings.isArray()) {
            throw new InvalidCommandException();
        }
        if (!workOrderId.toString().equals(text(workOrderSnapshot, "id"))
            || !tenantId.toString().equals(text(workOrderSnapshot, "tenant_id"))
            || workOrderVersion != number(workOrderSnapshot, "version")
            || !"COMPLETED".equals(text(workOrderSnapshot, "status"))) {
            throw new InvalidCommandException();
        }
        return this;
    }

    private static String text(JsonNode node, String name) {
        JsonNode value = node.get(name);
        if (value == null || !value.isTextual() || value.asText().isBlank()) {
            throw new InvalidCommandException();
        }
        return value.asText();
    }

    private static long number(JsonNode node, String name) {
        JsonNode value = node.get(name);
        if (value == null || !value.isIntegralNumber() || !value.canConvertToLong()) {
            throw new InvalidCommandException();
        }
        return value.asLong();
    }
}
