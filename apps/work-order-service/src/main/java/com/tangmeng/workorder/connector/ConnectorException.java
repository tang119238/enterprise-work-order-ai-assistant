package com.tangmeng.workorder.connector;

/**
 * Stable exception from connector operations.
 * Does not expose upstream response bodies.
 */
public class ConnectorException extends Exception {

    private final ErrorCode errorCode;

    public ConnectorException(ErrorCode errorCode, String message) {
        super(message);
        this.errorCode = errorCode;
    }

    public ConnectorException(ErrorCode errorCode, String message, Throwable cause) {
        super(message, cause);
        this.errorCode = errorCode;
    }

    public ErrorCode getErrorCode() {
        return errorCode;
    }

    public enum ErrorCode {
        NOT_FOUND,
        VERSION_CONFLICT,
        INVALID_STATE,
        INVALID_COMMAND,
        UPSTREAM_TIMEOUT,
        UPSTREAM_ERROR,
        IDEMPOTENCY_CONFLICT
    }
}
