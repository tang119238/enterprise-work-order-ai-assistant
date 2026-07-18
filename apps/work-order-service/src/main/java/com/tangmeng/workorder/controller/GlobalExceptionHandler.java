package com.tangmeng.workorder.controller;

import com.tangmeng.workorder.api.ApiError;
import com.tangmeng.workorder.command.ActionNotPermittedException;
import com.tangmeng.workorder.command.InvalidCommandException;
import com.tangmeng.workorder.service.InvalidStateTransitionException;
import com.tangmeng.workorder.service.WorkOrderNotFoundException;
import jakarta.validation.ConstraintViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.method.annotation.HandlerMethodValidationException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(InvalidCommandException.class)
    public ResponseEntity<ApiError> handleInvalidCommand(Exception exception) {
        return ResponseEntity.status(HttpStatus.UNPROCESSABLE_ENTITY)
            .body(ApiError.of("INVALID_COMMAND", "Invalid command"));
    }

    @ExceptionHandler(ActionNotPermittedException.class)
    public ResponseEntity<ApiError> handleActionNotPermitted(ActionNotPermittedException exception) {
        return ResponseEntity.status(HttpStatus.FORBIDDEN)
            .body(ApiError.of(ActionNotPermittedException.ERROR_CODE, "Action not permitted"));
    }

    @ExceptionHandler(InvalidStateTransitionException.class)
    public ResponseEntity<ApiError> handleInvalidTransition(InvalidStateTransitionException exception) {
        return ResponseEntity.status(HttpStatus.CONFLICT)
            .body(ApiError.of(InvalidStateTransitionException.ERROR_CODE, "Invalid state transition"));
    }

    @ExceptionHandler(WorkOrderNotFoundException.class)
    public ResponseEntity<ApiError> handleNotFound(WorkOrderNotFoundException exception) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
            .body(ApiError.of("WORK_ORDER_NOT_FOUND", exception.getMessage()));
    }

    @ExceptionHandler({
        HandlerMethodValidationException.class,
        ConstraintViolationException.class,
        MethodArgumentTypeMismatchException.class
    })
    public ResponseEntity<ApiError> handleInvalidQuery(Exception exception) {
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
            .body(ApiError.of("INVALID_QUERY_PARAMETER", "Invalid query parameter"));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiError> handleGeneric(Exception exception) {
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
            .body(ApiError.of("INTERNAL_ERROR", "Internal server error"));
    }
}
