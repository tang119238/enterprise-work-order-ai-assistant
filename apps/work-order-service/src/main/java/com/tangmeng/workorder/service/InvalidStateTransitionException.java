package com.tangmeng.workorder.service;

public class InvalidStateTransitionException extends RuntimeException {

    public static final String ERROR_CODE = "INVALID_STATE_TRANSITION";

    public InvalidStateTransitionException() {
        super(ERROR_CODE);
    }

    public String getErrorCode() {
        return ERROR_CODE;
    }
}
