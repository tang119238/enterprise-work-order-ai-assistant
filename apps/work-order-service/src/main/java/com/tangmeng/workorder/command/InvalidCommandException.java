package com.tangmeng.workorder.command;

public class InvalidCommandException extends RuntimeException {

    public static final String ERROR_CODE = "INVALID_COMMAND";

    public InvalidCommandException() {
        super(ERROR_CODE);
    }

    public InvalidCommandException(Throwable cause) {
        super(ERROR_CODE, cause);
    }
}
