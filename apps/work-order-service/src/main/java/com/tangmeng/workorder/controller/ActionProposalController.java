package com.tangmeng.workorder.controller;

import com.tangmeng.workorder.api.ActionProposalRequest;
import com.tangmeng.workorder.api.ActionProposalResponse;
import com.tangmeng.workorder.command.ActionProposalService;
import com.tangmeng.workorder.security.TenantContext;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/action-proposals")
@RequiredArgsConstructor
public class ActionProposalController {

    private final ActionProposalService service;

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ActionProposalResponse create(
        Authentication authentication,
        @RequestBody ActionProposalRequest request
    ) {
        return service.create(tenantContext(authentication), request.toCommand());
    }

    private static TenantContext tenantContext(Authentication authentication) {
        if (authentication != null && authentication.getDetails() instanceof TenantContext context) {
            return context;
        }
        throw new IllegalStateException("Verified tenant context is missing");
    }
}
