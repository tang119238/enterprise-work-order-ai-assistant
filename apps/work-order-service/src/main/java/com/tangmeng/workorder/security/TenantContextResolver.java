package com.tangmeng.workorder.security;

import com.tangmeng.workorder.tenant.TenantAccessService;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Component;

import java.util.Collection;
import java.util.LinkedHashSet;
import java.util.Set;
import java.util.UUID;

@Component
public class TenantContextResolver {

    private final TenantAccessService access;

    public TenantContextResolver(TenantAccessService access) {
        this.access = access;
    }

    public TenantContext resolve(Jwt jwt) {
        if (jwt == null) {
            throw invalid("JWT is required");
        }
        String subject = requiredString(jwt.getClaims().get("sub"), "sub subject");
        UUID tenantId = requiredUuid(jwt.getClaims().get("tenant_id"), "tenant_id");
        Set<String> tokenRoles = stringSet(jwt.getClaims().get("roles"), "roles");
        Set<UUID> tokenProjects = uuidSet(jwt.getClaims().get("project_ids"), "project_ids");

        UUID userId = access.loadCurrentUserId(tenantId, subject);
        if (userId == null) {
            throw invalid("No current user identity");
        }

        Set<String> roles = intersection(tokenRoles, safe(access.loadCurrentRoles(tenantId, subject)));
        Set<UUID> projects = intersection(tokenProjects, safe(access.loadCurrentProjects(tenantId, subject)));
        String requestId = optionalIdentifier(jwt.getClaims().get("request_id"));
        String traceId = optionalIdentifier(jwt.getClaims().get("trace_id"));

        return new TenantContext(tenantId, userId, subject, roles, projects, requestId, traceId);
    }

    private static String requiredString(Object value, String claim) {
        if (!(value instanceof String text) || text.isBlank()) {
            throw invalid("Invalid " + claim + " claim");
        }
        return text;
    }

    private static UUID requiredUuid(Object value, String claim) {
        String text = requiredString(value, claim);
        try {
            return UUID.fromString(text);
        } catch (IllegalArgumentException exception) {
            throw invalid("Invalid " + claim + " claim");
        }
    }

    private static Set<String> stringSet(Object value, String claim) {
        if (!(value instanceof Collection<?> collection)) {
            throw invalid("Invalid " + claim + " claim");
        }
        LinkedHashSet<String> result = new LinkedHashSet<>();
        for (Object item : collection) {
            result.add(requiredString(item, claim));
        }
        return result;
    }

    private static Set<UUID> uuidSet(Object value, String claim) {
        if (!(value instanceof Collection<?> collection)) {
            throw invalid("Invalid " + claim + " claim");
        }
        LinkedHashSet<UUID> result = new LinkedHashSet<>();
        for (Object item : collection) {
            result.add(requiredUuid(item, claim));
        }
        return result;
    }

    private static String optionalIdentifier(Object value) {
        if (value == null) {
            return UUID.randomUUID().toString();
        }
        return requiredString(value, "identifier");
    }

    private static <T> Set<T> safe(Set<T> values) {
        return values == null ? Set.of() : values;
    }

    private static <T> Set<T> intersection(Set<T> tokenValues, Set<T> databaseValues) {
        LinkedHashSet<T> result = new LinkedHashSet<>(tokenValues);
        result.retainAll(databaseValues);
        return result;
    }

    private static BadCredentialsException invalid(String message) {
        return new BadCredentialsException(message);
    }
}
