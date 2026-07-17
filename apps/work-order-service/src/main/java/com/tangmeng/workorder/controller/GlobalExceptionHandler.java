package com.tangmeng.workorder.controller;

import com.tangmeng.workorder.api.ApiError;
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
}
