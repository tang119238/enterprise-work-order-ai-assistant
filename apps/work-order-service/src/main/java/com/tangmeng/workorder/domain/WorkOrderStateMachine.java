package com.tangmeng.workorder.domain;

import com.tangmeng.workorder.service.InvalidStateTransitionException;

import java.time.LocalDateTime;
import java.util.Collections;
import java.util.EnumMap;
import java.util.Map;

public class WorkOrderStateMachine {

    private static final Map<WorkOrderStatus, Map<WorkOrderAction, WorkOrderStatus>> TRANSITIONS = transitions();

    /**
     * Applies a domain action using the caller-supplied occurrence time to record ACCEPT deterministically.
     * The occurrence time is required only for ACCEPT and is otherwise not used.
     */
    public WorkOrderTransitionResult transition(
        WorkOrderSnapshot current,
        WorkOrderAction action,
        LocalDateTime occurredAt
    ) {
        if (current == null || current.status() == null || action == null) {
            throw invalidTransition();
        }

        WorkOrderStatus currentStatus = current.status();
        assertMutable(currentStatus);

        WorkOrderStatus nextStatus = TRANSITIONS.getOrDefault(currentStatus, Map.of()).get(action);
        if (nextStatus == null) {
            throw invalidTransition();
        }

        LocalDateTime acceptedAt = current.acceptedAt();
        String cancelReason = current.cancelReason();
        switch (action) {
            case ACCEPT -> acceptedAt = recordAcceptance(current, occurredAt);
            case START -> requireAcceptanceRecorded(current);
            case CANCEL -> cancelReason = normalizedCancellationReason(current);
            case ASSIGN, COMPLETE, CLOSE -> {
            }
        }
        return new WorkOrderTransitionResult(nextStatus, acceptedAt, cancelReason);
    }

    public void assertMutable(WorkOrderStatus status) {
        if (status == null || status == WorkOrderStatus.CLOSED || status == WorkOrderStatus.CANCELLED) {
            throw invalidTransition();
        }
    }

    private LocalDateTime recordAcceptance(WorkOrderSnapshot current, LocalDateTime occurredAt) {
        if (current.acceptedAt() != null || occurredAt == null) {
            throw invalidTransition();
        }
        return occurredAt;
    }

    private void requireAcceptanceRecorded(WorkOrderSnapshot current) {
        if (current.acceptedAt() == null) {
            throw invalidTransition();
        }
    }

    private String normalizedCancellationReason(WorkOrderSnapshot current) {
        if (current.cancelReason() == null || current.cancelReason().isBlank()) {
            throw invalidTransition();
        }
        return current.cancelReason().strip();
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
