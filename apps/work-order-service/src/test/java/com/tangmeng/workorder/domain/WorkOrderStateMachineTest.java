package com.tangmeng.workorder.domain;

import com.tangmeng.workorder.service.InvalidStateTransitionException;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;

import java.time.LocalDateTime;
import java.util.EnumSet;
import java.util.Set;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.junit.jupiter.params.provider.Arguments.arguments;
import static com.tangmeng.workorder.domain.WorkOrderAction.ACCEPT;
import static com.tangmeng.workorder.domain.WorkOrderAction.ASSIGN;
import static com.tangmeng.workorder.domain.WorkOrderAction.CANCEL;
import static com.tangmeng.workorder.domain.WorkOrderAction.CLOSE;
import static com.tangmeng.workorder.domain.WorkOrderAction.COMPLETE;
import static com.tangmeng.workorder.domain.WorkOrderAction.START;
import static com.tangmeng.workorder.domain.WorkOrderStatus.CANCELLED;
import static com.tangmeng.workorder.domain.WorkOrderStatus.CLOSED;
import static com.tangmeng.workorder.domain.WorkOrderStatus.COMPLETED;
import static com.tangmeng.workorder.domain.WorkOrderStatus.PENDING_ACCEPTANCE;
import static com.tangmeng.workorder.domain.WorkOrderStatus.PENDING_DISPATCH;
import static com.tangmeng.workorder.domain.WorkOrderStatus.PROCESSING;

class WorkOrderStateMachineTest {

    private final WorkOrderStateMachine stateMachine = new WorkOrderStateMachine();
    private static final LocalDateTime OCCURRED_AT = LocalDateTime.of(2026, 7, 18, 10, 15);

    static Stream<Arguments> allowed() {
        return Stream.of(
            arguments(PENDING_DISPATCH, ASSIGN, PENDING_ACCEPTANCE),
            arguments(PENDING_ACCEPTANCE, ACCEPT, PENDING_ACCEPTANCE),
            arguments(PENDING_ACCEPTANCE, START, PROCESSING),
            arguments(PROCESSING, COMPLETE, COMPLETED),
            arguments(COMPLETED, CLOSE, CLOSED),
            arguments(PENDING_DISPATCH, CANCEL, CANCELLED),
            arguments(PENDING_ACCEPTANCE, CANCEL, CANCELLED),
            arguments(PROCESSING, CANCEL, CANCELLED),
            arguments(COMPLETED, CANCEL, CANCELLED));
    }

    @ParameterizedTest
    @MethodSource("allowed")
    void transitionsEachAllowedStatusActionPair(
        WorkOrderStatus status,
        WorkOrderAction action,
        WorkOrderStatus expectedStatus
    ) {
        assertThat(stateMachine.transition(snapshotFor(status, action), action, OCCURRED_AT).status())
            .isEqualTo(expectedStatus);
    }

    static Stream<Arguments> rejected() {
        Set<Transition> allowedTransitions = Set.of(
            new Transition(PENDING_DISPATCH, ASSIGN),
            new Transition(PENDING_ACCEPTANCE, ACCEPT),
            new Transition(PENDING_ACCEPTANCE, START),
            new Transition(PROCESSING, COMPLETE),
            new Transition(COMPLETED, CLOSE),
            new Transition(PENDING_DISPATCH, CANCEL),
            new Transition(PENDING_ACCEPTANCE, CANCEL),
            new Transition(PROCESSING, CANCEL),
            new Transition(COMPLETED, CANCEL));

        return EnumSet.allOf(WorkOrderStatus.class).stream()
            .flatMap(status -> EnumSet.allOf(WorkOrderAction.class).stream()
                .map(action -> new Transition(status, action)))
            .filter(transition -> !allowedTransitions.contains(transition))
            .map(transition -> arguments(transition.status(), transition.action()));
    }

    @ParameterizedTest
    @MethodSource("rejected")
    void rejectsEveryOtherStatusActionPair(WorkOrderStatus status, WorkOrderAction action) {
        assertInvalidTransition(() -> stateMachine.transition(snapshotFor(status, action), action, OCCURRED_AT));
    }

    @Test
    void rejectsAcceptWhenAcceptanceIsAlreadyRecorded() {
        assertInvalidTransition(() -> stateMachine.transition(
            new WorkOrderSnapshot(PENDING_ACCEPTANCE, LocalDateTime.of(2026, 7, 18, 9, 30), null),
            ACCEPT,
            OCCURRED_AT));
    }

    @Test
    void rejectsAcceptWithoutAnExplicitTimestamp() {
        assertInvalidTransition(() -> stateMachine.transition(
            new WorkOrderSnapshot(PENDING_ACCEPTANCE, null, null),
            ACCEPT,
            null));
    }

    @Test
    void rejectsStartWhenAcceptanceIsMissing() {
        assertInvalidTransition(() -> stateMachine.transition(
            new WorkOrderSnapshot(PENDING_ACCEPTANCE, null, null),
            START,
            OCCURRED_AT));
    }

    @ParameterizedTest
    @org.junit.jupiter.params.provider.NullAndEmptySource
    @org.junit.jupiter.params.provider.ValueSource(strings = {" ", "\t"})
    void rejectsCancellationWithoutANonblankReason(String cancelReason) {
        assertInvalidTransition(() -> stateMachine.transition(
            new WorkOrderSnapshot(PROCESSING, null, cancelReason),
            CANCEL,
            OCCURRED_AT));
    }

    @Test
    void acceptRecordsTheExplicitTimestampWhileRetainingPendingAcceptance() {
        WorkOrderTransitionResult result = stateMachine.transition(
            new WorkOrderSnapshot(PENDING_ACCEPTANCE, null, null),
            ACCEPT,
            OCCURRED_AT);

        assertThat(result).isEqualTo(new WorkOrderTransitionResult(PENDING_ACCEPTANCE, OCCURRED_AT, null));
    }

    @Test
    void startCarriesAcceptanceTimestampIntoProcessing() {
        LocalDateTime acceptedAt = LocalDateTime.of(2026, 7, 18, 9, 30);

        WorkOrderTransitionResult result = stateMachine.transition(
            new WorkOrderSnapshot(PENDING_ACCEPTANCE, acceptedAt, null),
            START,
            OCCURRED_AT);

        assertThat(result).isEqualTo(new WorkOrderTransitionResult(PROCESSING, acceptedAt, null));
    }

    @Test
    void cancelReturnsANormalizedReason() {
        WorkOrderTransitionResult result = stateMachine.transition(
            new WorkOrderSnapshot(PROCESSING, null, "  Customer request  "),
            CANCEL,
            OCCURRED_AT);

        assertThat(result).isEqualTo(new WorkOrderTransitionResult(CANCELLED, null, "Customer request"));
    }

    @Test
    void rejectsANullSnapshot() {
        assertInvalidTransition(() -> stateMachine.transition(null, ASSIGN, OCCURRED_AT));
    }

    @Test
    void rejectsANullStatus() {
        assertInvalidTransition(() -> stateMachine.transition(
            new WorkOrderSnapshot(null, null, null),
            ASSIGN,
            OCCURRED_AT));
    }

    @Test
    void rejectsANullAction() {
        assertInvalidTransition(() -> stateMachine.transition(
            new WorkOrderSnapshot(PENDING_DISPATCH, null, null),
            null,
            OCCURRED_AT));
    }

    @ParameterizedTest
    @MethodSource("terminalStatuses")
    void rejectsMutationsForTerminalStates(WorkOrderStatus status) {
        assertInvalidTransition(() -> stateMachine.assertMutable(status));
    }

    static Stream<WorkOrderStatus> terminalStatuses() {
        return Stream.of(CLOSED, CANCELLED);
    }

    private WorkOrderSnapshot snapshotFor(WorkOrderStatus status, WorkOrderAction action) {
        LocalDateTime acceptedAt = action == START ? LocalDateTime.of(2026, 7, 18, 9, 30) : null;
        String cancelReason = action == CANCEL ? "Customer request" : null;
        return new WorkOrderSnapshot(status, acceptedAt, cancelReason);
    }

    private void assertInvalidTransition(org.assertj.core.api.ThrowableAssert.ThrowingCallable transition) {
        assertThatThrownBy(transition)
            .isInstanceOf(InvalidStateTransitionException.class)
            .extracting(exception -> ((InvalidStateTransitionException) exception).getErrorCode())
            .isEqualTo("INVALID_STATE_TRANSITION");
    }

    private record Transition(WorkOrderStatus status, WorkOrderAction action) {
    }
}
