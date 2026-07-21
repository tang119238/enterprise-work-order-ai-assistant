package com.tangmeng.workorder.command;

public class IdempotencyConflictException extends RuntimeException {
    public static final String ERROR_CODE = "IDEMPOTENCY_KEY_CONFLICT";
    public IdempotencyConflictException() { super(ERROR_CODE); }
}
