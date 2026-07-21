package com.tangmeng.workorder.quality;

import com.tangmeng.workorder.command.InvalidCommandException;
import com.tangmeng.workorder.security.TenantContext;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/internal/quality-results")
@RequiredArgsConstructor
public class QualityResultController {
    private final RectificationService service;

    @PostMapping
    public QualityResultCallbackResponse accept(
        Authentication authentication,
        @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey,
        @RequestBody QualityResultCallback callback
    ) {
        if (idempotencyKey == null || idempotencyKey.isBlank()) {
            throw new InvalidCommandException();
        }
        return service.accept(tenantContext(authentication), callback, idempotencyKey.strip());
    }

    private static TenantContext tenantContext(Authentication authentication) {
        if (authentication != null && authentication.getDetails() instanceof TenantContext context) {
            return context;
        }
        throw new IllegalStateException("Verified tenant context is missing");
    }
}
