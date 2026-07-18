package com.tangmeng.workorder.security;

import com.tangmeng.workorder.tenant.TenantAccessService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.security.oauth2.core.OAuth2TokenValidatorResult;
import org.springframework.security.oauth2.jwt.Jwt;

import java.time.Instant;
import java.util.List;
import java.util.Set;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class TenantContextResolverTest {

    private static final UUID TENANT = UUID.fromString("11111111-1111-1111-1111-111111111111");
    private static final UUID USER = UUID.fromString("00000000-0000-0000-0000-000000009001");
    private static final UUID PROJECT_A = UUID.fromString("00000000-0000-0000-0000-000000010001");
    private static final UUID PROJECT_B = UUID.fromString("00000000-0000-0000-0000-000000010002");

    @Mock
    private TenantAccessService access;

    @Test
    void intersectsTokenAndDatabaseAuthority() {
        Jwt jwt = jwt("dispatcher-1", TENANT.toString(),
            List.of("DISPATCHER", "TENANT_ADMIN"),
            List.of(PROJECT_A.toString(), PROJECT_B.toString()));
        when(access.loadCurrentUserId(TENANT, "dispatcher-1")).thenReturn(USER);
        when(access.loadCurrentRoles(TENANT, "dispatcher-1")).thenReturn(Set.of("DISPATCHER"));
        when(access.loadCurrentProjects(TENANT, "dispatcher-1")).thenReturn(Set.of(PROJECT_A));

        TenantContext context = new TenantContextResolver(access).resolve(jwt);

        assertThat(context.tenantId()).isEqualTo(TENANT);
        assertThat(context.userId()).isEqualTo(USER);
        assertThat(context.subject()).isEqualTo("dispatcher-1");
        assertThat(context.roles()).containsExactly("DISPATCHER");
        assertThat(context.projectIds()).containsExactly(PROJECT_A);
        assertThat(context.requestId()).isNotBlank();
        assertThat(context.traceId()).isNotBlank();
    }

    @Test
    void rejectsBlankSubjectBeforeDatabaseLookup() {
        Jwt jwt = jwt(" ", TENANT.toString(), List.of("DISPATCHER"), List.of(PROJECT_A.toString()));

        assertThatThrownBy(() -> new TenantContextResolver(access).resolve(jwt))
            .isInstanceOf(BadCredentialsException.class)
            .hasMessageContaining("subject");
        verifyNoInteractions(access);
    }

    @Test
    void rejectsMalformedTenantBeforeDatabaseLookup() {
        Jwt jwt = jwt("dispatcher-1", "not-a-uuid", List.of("DISPATCHER"), List.of(PROJECT_A.toString()));

        assertThatThrownBy(() -> new TenantContextResolver(access).resolve(jwt))
            .isInstanceOf(BadCredentialsException.class)
            .hasMessageContaining("tenant_id");
        verifyNoInteractions(access);
    }

    @Test
    void rejectsMalformedRoleAndProjectCollections() {
        Jwt jwt = jwt("dispatcher-1", TENANT.toString(), "DISPATCHER", List.of("not-a-uuid"));

        assertThatThrownBy(() -> new TenantContextResolver(access).resolve(jwt))
            .isInstanceOf(BadCredentialsException.class);
        verifyNoInteractions(access);
    }

    @Test
    void failsClosedWhenCurrentUserIsAbsent() {
        Jwt jwt = jwt("deleted-user", TENANT.toString(), List.of("DISPATCHER"), List.of(PROJECT_A.toString()));
        when(access.loadCurrentUserId(TENANT, "deleted-user")).thenReturn(null);

        assertThatThrownBy(() -> new TenantContextResolver(access).resolve(jwt))
            .isInstanceOf(BadCredentialsException.class)
            .hasMessageContaining("current user");
    }

    @Test
    void requiresConfiguredIssuerAndAudience() {
        Instant now = Instant.now();
        Jwt valid = Jwt.withTokenValue("valid")
            .header("alg", "RS256")
            .issuer("https://issuer.example")
            .audience(List.of("work-order-service"))
            .subject("dispatcher-1")
            .issuedAt(now.minusSeconds(10))
            .expiresAt(now.plusSeconds(60))
            .build();
        Jwt wrongAudience = Jwt.withTokenValue("wrong-audience")
            .header("alg", "RS256")
            .issuer("https://issuer.example")
            .audience(List.of("another-service"))
            .subject("dispatcher-1")
            .issuedAt(now.minusSeconds(10))
            .expiresAt(now.plusSeconds(60))
            .build();
        Jwt wrongIssuer = Jwt.withTokenValue("wrong-issuer")
            .header("alg", "RS256")
            .issuer("https://other-issuer.example")
            .audience(List.of("work-order-service"))
            .subject("dispatcher-1")
            .issuedAt(now.minusSeconds(10))
            .expiresAt(now.plusSeconds(60))
            .build();

        OAuth2TokenValidatorResult validResult = SecurityConfig
            .jwtValidator("https://issuer.example", "work-order-service").validate(valid);
        OAuth2TokenValidatorResult invalidResult = SecurityConfig
            .jwtValidator("https://issuer.example", "work-order-service").validate(wrongAudience);
        OAuth2TokenValidatorResult wrongIssuerResult = SecurityConfig
            .jwtValidator("https://issuer.example", "work-order-service").validate(wrongIssuer);

        assertThat(validResult.hasErrors()).isFalse();
        assertThat(invalidResult.hasErrors()).isTrue();
        assertThat(wrongIssuerResult.hasErrors()).isTrue();
    }

    private static Jwt jwt(String subject, String tenantId, Object roles, Object projects) {
        return Jwt.withTokenValue("test")
            .header("alg", "none")
            .claim("iss", "https://issuer.example")
            .claim("sub", subject)
            .claim("tenant_id", tenantId)
            .claim("roles", roles)
            .claim("project_ids", projects)
            .claim("scope", "work-order:write")
            .build();
    }
}
