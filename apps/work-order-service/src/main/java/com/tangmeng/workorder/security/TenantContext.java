package com.tangmeng.workorder.security;

import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.Set;
import java.util.UUID;

public record TenantContext(
    UUID tenantId,
    UUID userId,
    String subject,
    Set<String> roles,
    Set<UUID> projectIds,
    String requestId,
    String traceId
) {

    public TenantContext {
        roles = immutableCopy(roles);
        projectIds = immutableCopy(projectIds);
    }

    private static <T> Set<T> immutableCopy(Set<T> values) {
        return Collections.unmodifiableSet(new LinkedHashSet<>(values));
    }
}
