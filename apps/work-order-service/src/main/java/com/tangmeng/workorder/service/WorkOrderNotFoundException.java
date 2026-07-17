package com.tangmeng.workorder.service;

public class WorkOrderNotFoundException extends RuntimeException {

    private final String workOrderNo;

    public WorkOrderNotFoundException(String workOrderNo) {
        super("Work order not found: " + workOrderNo);
        this.workOrderNo = workOrderNo;
    }

    public String getWorkOrderNo() {
        return workOrderNo;
    }
}
