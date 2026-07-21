package com.tangmeng.workorder.controller;

import com.tangmeng.workorder.api.ActionProposalRequest;
import com.tangmeng.workorder.api.ActionProposalResponse;
import com.tangmeng.workorder.api.ApiError;
import com.tangmeng.workorder.api.ConfirmProposalRequest;
import com.tangmeng.workorder.api.WorkOrderExecutionResponse;
import com.tangmeng.workorder.command.InvalidCommandException;
import com.tangmeng.workorder.command.ActionProposalService;
import com.tangmeng.workorder.security.TenantContext;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/action-proposals")
@RequiredArgsConstructor
public class ActionProposalController {

    private final ActionProposalService service;

    @ExceptionHandler(HttpMessageNotReadableException.class)
    @ResponseStatus(HttpStatus.UNPROCESSABLE_ENTITY)
    public ApiError invalidBody(HttpMessageNotReadableException exception) {
        return ApiError.of("INVALID_COMMAND", "Invalid command");
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ActionProposalResponse create(
        Authentication authentication,
        @RequestBody ActionProposalRequest request
    ) {
        return service.create(tenantContext(authentication), request.toCommand());
    }

    @PostMapping("/{id}/confirm")
    public WorkOrderExecutionResponse confirm(
        Authentication authentication,
        @PathVariable java.util.UUID id,
        @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey,
        @RequestBody ConfirmProposalRequest request
    ) {
        if (idempotencyKey == null || idempotencyKey.isBlank()) throw new InvalidCommandException();
        return service.confirm(tenantContext(authentication), id, request.requireConfirm(), idempotencyKey.strip());
    }

    @PostMapping("/{id}/reject")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void reject(
        Authentication authentication,
        @PathVariable java.util.UUID id,
        @RequestBody ConfirmProposalRequest request
    ) {
        service.reject(tenantContext(authentication), id, request.requireReject());
    }

    private static TenantContext tenantContext(Authentication authentication) {
        if (authentication != null && authentication.getDetails() instanceof TenantContext context) {
            return context;
        }
        throw new IllegalStateException("Verified tenant context is missing");
    }
}
