package com.tangmeng.workorder.connector;

import java.util.UUID;

/**
 * Result of a write operation through the connector.
 */
public record ConnectorResult(
    UUID workOrderId,
    ResultStatus status,
    long newVersion,
    String message
) {
    public enum ResultStatus {
        CONFIRMED,
        REJECTED,
        UNKNOWN
    }

    public static ConnectorResult confirmed(UUID workOrderId, long newVersion) {
        return new ConnectorResult(workOrderId, ResultStatus.CONFIRMED, newVersion, null);
    }

    public static ConnectorResult rejected(UUID workOrderId, String message) {
        return new ConnectorResult(workOrderId, ResultStatus.REJECTED, 0, message);
    }

    public static ConnectorResult unknown(UUID workOrderId, String message) {
        return new ConnectorResult(workOrderId, ResultStatus.UNKNOWN, 0, message);
    }
}
