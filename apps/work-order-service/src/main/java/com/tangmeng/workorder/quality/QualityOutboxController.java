package com.tangmeng.workorder.quality;

import com.tangmeng.workorder.security.TenantContext;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.UUID;

@RestController
@RequestMapping("/internal/quality-events")
@RequiredArgsConstructor
public class QualityOutboxController {

    private final QualityOutboxService service;

    @PostMapping("/claim")
    public List<QualityOutboxService.ClaimedQualityEvent> claim(
        Authentication authentication,
        @Valid @RequestBody ClaimRequest request
    ) {
        return service.claim(tenantContext(authentication), request.limit());
    }

    @PostMapping("/{eventId}/ack")
    public ResponseEntity<Void> acknowledge(
        Authentication authentication,
        @PathVariable UUID eventId
    ) {
        return service.acknowledge(tenantContext(authentication), eventId)
            ? ResponseEntity.noContent().build()
            : ResponseEntity.notFound().build();
    }

    private static TenantContext tenantContext(Authentication authentication) {
        if (authentication != null && authentication.getDetails() instanceof TenantContext context) {
            return context;
        }
        throw new IllegalStateException("Verified tenant context is missing");
    }

    public record ClaimRequest(@Min(1) @Max(50) int limit) {
    }
}
