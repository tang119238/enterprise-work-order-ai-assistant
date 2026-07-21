package com.tangmeng.workorder.command;

public class ActionNotPermittedException extends RuntimeException {

    public static final String ERROR_CODE = "ACTION_NOT_PERMITTED";

    public ActionNotPermittedException() {
        super(ERROR_CODE);
    }
}
