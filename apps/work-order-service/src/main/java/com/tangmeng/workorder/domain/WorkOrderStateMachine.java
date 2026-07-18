package com.tangmeng.workorder.domain;

import com.tangmeng.workorder.service.InvalidStateTransitionException;

import java.util.Collections;
import java.util.EnumMap;
import java.util.Map;

public class WorkOrderStateMachine {

    private static final Map<WorkOrderStatus, Map<WorkOrderAction, WorkOrderStatus>> TRANSITIONS = transitions();

    public WorkOrderStatus transition(WorkOrderSnapshot current, WorkOrderAction action) {
        WorkOrderStatus currentStatus = current.status();
        assertMutable(currentStatus);

        WorkOrderStatus nextStatus = TRANSITIONS.getOrDefault(currentStatus, Map.of()).get(action);
        if (nextStatus == null) {
            throw invalidTransition();
        }

        switch (action) {
            case ACCEPT -> requireAcceptanceNotRecorded(current);
            case START -> requireAcceptanceRecorded(current);
            case CANCEL -> requireCancellationReason(current);
            case ASSIGN, COMPLETE, CLOSE -> {
            }
        }
        return nextStatus;
    }

    public void assertMutable(WorkOrderStatus status) {
        if (status == WorkOrderStatus.CLOSED || status == WorkOrderStatus.CANCELLED) {
            throw invalidTransition();
        }
    }

    private void requireAcceptanceNotRecorded(WorkOrderSnapshot current) {
        if (current.acceptedAt() != null) {
            throw invalidTransition();
        }
    }

    private void requireAcceptanceRecorded(WorkOrderSnapshot current) {
        if (current.acceptedAt() == null) {
            throw invalidTransition();
        }
    }

    private void requireCancellationReason(WorkOrderSnapshot current) {
        if (current.cancelReason() == null || current.cancelReason().isBlank()) {
            throw invalidTransition();
        }
    }

    private InvalidStateTransitionException invalidTransition() {
        return new InvalidStateTransitionException();
    }

    private static Map<WorkOrderStatus, Map<WorkOrderAction, WorkOrderStatus>> transitions() {
        EnumMap<WorkOrderStatus, Map<WorkOrderAction, WorkOrderStatus>> transitions = new EnumMap<>(WorkOrderStatus.class);
        transitions.put(WorkOrderStatus.PENDING_DISPATCH, Map.of(
            WorkOrderAction.ASSIGN, WorkOrderStatus.PENDING_ACCEPTANCE,
            WorkOrderAction.CANCEL, WorkOrderStatus.CANCELLED));
        transitions.put(WorkOrderStatus.PENDING_ACCEPTANCE, Map.of(
            WorkOrderAction.ACCEPT, WorkOrderStatus.PENDING_ACCEPTANCE,
            WorkOrderAction.START, WorkOrderStatus.PROCESSING,
            WorkOrderAction.CANCEL, WorkOrderStatus.CANCELLED));
        transitions.put(WorkOrderStatus.PROCESSING, Map.of(
            WorkOrderAction.COMPLETE, WorkOrderStatus.COMPLETED,
            WorkOrderAction.CANCEL, WorkOrderStatus.CANCELLED));
        transitions.put(WorkOrderStatus.COMPLETED, Map.of(
            WorkOrderAction.CLOSE, WorkOrderStatus.CLOSED,
            WorkOrderAction.CANCEL, WorkOrderStatus.CANCELLED));
        return Collections.unmodifiableMap(transitions);
    }
}
