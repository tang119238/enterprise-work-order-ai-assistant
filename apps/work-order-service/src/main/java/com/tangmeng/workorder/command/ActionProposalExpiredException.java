package com.tangmeng.workorder.command;

public class ActionProposalExpiredException extends RuntimeException {
    public static final String ERROR_CODE = "ACTION_PROPOSAL_EXPIRED";
    public ActionProposalExpiredException() { super(ERROR_CODE); }
}
