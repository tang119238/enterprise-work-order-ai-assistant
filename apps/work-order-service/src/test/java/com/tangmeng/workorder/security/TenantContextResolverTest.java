package com.tangmeng.workorder.security;

import com.tangmeng.workorder.tenant.TenantAccessService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.security.core.Authentication;
import org.springframework.security.oauth2.core.OAuth2TokenValidatorResult;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.core.io.ClassPathResource;

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
        assertThat(context.scopes()).containsExactly("work-order:write");
        assertThat(context.requestId()).isNotBlank();
        assertThat(context.traceId()).isNotBlank();
        assertThatThrownBy(() -> context.scopes().add("work-order:admin"))
            .isInstanceOf(UnsupportedOperationException.class);
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
    void normalizesStringAndCollectionScopes() {
        when(access.loadCurrentUserId(TENANT, "dispatcher-1")).thenReturn(USER);
        when(access.loadCurrentRoles(TENANT, "dispatcher-1")).thenReturn(Set.of("DISPATCHER"));
        when(access.loadCurrentProjects(TENANT, "dispatcher-1")).thenReturn(Set.of(PROJECT_A));
        TenantContextResolver resolver = new TenantContextResolver(access);

        TenantContext stringContext = resolver.resolve(jwt("dispatcher-1", TENANT.toString(),
            List.of("DISPATCHER"), List.of(PROJECT_A.toString()), "  work-order:read   work-order:write  "));
        TenantContext collectionContext = resolver.resolve(jwt("dispatcher-1", TENANT.toString(),
            List.of("DISPATCHER"), List.of(PROJECT_A.toString()),
            List.of("work-order:read", "work-order:write")));

        assertThat(stringContext.scopes()).containsExactly("work-order:read", "work-order:write");
        assertThat(collectionContext.scopes()).containsExactly("work-order:read", "work-order:write");
    }

    @Test
    void rejectsMissingBlankWrongTypeAndBlankMemberScopes() {
        Jwt missing = jwt("dispatcher-1", TENANT.toString(), List.of("DISPATCHER"),
            List.of(PROJECT_A.toString()), null);
        Jwt blank = jwt("dispatcher-1", TENANT.toString(), List.of("DISPATCHER"),
            List.of(PROJECT_A.toString()), "  ");
        Jwt wrongType = jwt("dispatcher-1", TENANT.toString(), List.of("DISPATCHER"),
            List.of(PROJECT_A.toString()), 42);
        Jwt blankMember = jwt("dispatcher-1", TENANT.toString(), List.of("DISPATCHER"),
            List.of(PROJECT_A.toString()), List.of("work-order:read", " "));
        TenantContextResolver resolver = new TenantContextResolver(access);

        assertThatThrownBy(() -> resolver.resolve(missing)).isInstanceOf(BadCredentialsException.class);
        assertThatThrownBy(() -> resolver.resolve(blank)).isInstanceOf(BadCredentialsException.class);
        assertThatThrownBy(() -> resolver.resolve(wrongType)).isInstanceOf(BadCredentialsException.class);
        assertThatThrownBy(() -> resolver.resolve(blankMember)).isInstanceOf(BadCredentialsException.class);
        verifyNoInteractions(access);
    }

    @Test
    void preservesScopesAsAuthenticationAuthorities() {
        Jwt jwt = jwt("dispatcher-1", TENANT.toString(), List.of("DISPATCHER"),
            List.of(PROJECT_A.toString()), "work-order:read work-order:write");
        when(access.loadCurrentUserId(TENANT, "dispatcher-1")).thenReturn(USER);
        when(access.loadCurrentRoles(TENANT, "dispatcher-1")).thenReturn(Set.of("DISPATCHER"));
        when(access.loadCurrentProjects(TENANT, "dispatcher-1")).thenReturn(Set.of(PROJECT_A));

        Authentication authentication = SecurityConfig
            .jwtAuthenticationConverter(new TenantContextResolver(access)).convert(jwt);

        assertThat(authentication.getAuthorities())
            .extracting("authority")
            .containsExactly("DISPATCHER", "SCOPE_work-order:read", "SCOPE_work-order:write");
        assertThat(authentication.getDetails()).isInstanceOf(TenantContext.class);
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
        Jwt expired = Jwt.withTokenValue("expired")
            .header("alg", "RS256")
            .issuer("https://issuer.example")
            .audience(List.of("work-order-service"))
            .subject("dispatcher-1")
            .issuedAt(now.minusSeconds(120))
            .expiresAt(now.minusSeconds(60))
            .build();
        Jwt notYetValid = Jwt.withTokenValue("not-yet-valid")
            .header("alg", "RS256")
            .issuer("https://issuer.example")
            .audience(List.of("work-order-service"))
            .subject("dispatcher-1")
            .issuedAt(now)
            .notBefore(now.plusSeconds(120))
            .expiresAt(now.plusSeconds(300))
            .build();

        OAuth2TokenValidatorResult validResult = SecurityConfig
            .jwtValidator("https://issuer.example", "work-order-service").validate(valid);
        OAuth2TokenValidatorResult invalidResult = SecurityConfig
            .jwtValidator("https://issuer.example", "work-order-service").validate(wrongAudience);
        OAuth2TokenValidatorResult wrongIssuerResult = SecurityConfig
            .jwtValidator("https://issuer.example", "work-order-service").validate(wrongIssuer);
        OAuth2TokenValidatorResult expiredResult = SecurityConfig
            .jwtValidator("https://issuer.example", "work-order-service").validate(expired);
        OAuth2TokenValidatorResult notYetValidResult = SecurityConfig
            .jwtValidator("https://issuer.example", "work-order-service").validate(notYetValid);

        assertThat(validResult.hasErrors()).isFalse();
        assertThat(invalidResult.hasErrors()).isTrue();
        assertThat(wrongIssuerResult.hasErrors()).isTrue();
        assertThat(expiredResult.hasErrors()).isTrue();
        assertThat(notYetValidResult.hasErrors()).isTrue();
    }

    @Test
    void buildsDefaultDecoderFromLocalPublicKeyWithoutIssuerDiscovery() {
        JwtDecoder decoder = new SecurityConfig().jwtDecoder(
            "https://issuer.example",
            "work-order-service",
            "",
            new ClassPathResource("security/dev-jwt-public-key.pem")
        );

        assertThat(decoder).isNotNull();
    }

    private static Jwt jwt(String subject, String tenantId, Object roles, Object projects) {
        return jwt(subject, tenantId, roles, projects, "work-order:write");
    }

    private static Jwt jwt(
        String subject,
        String tenantId,
        Object roles,
        Object projects,
        Object scope
    ) {
        Jwt.Builder builder = Jwt.withTokenValue("test")
            .header("alg", "none")
            .claim("iss", "https://issuer.example")
            .claim("sub", subject)
            .claim("tenant_id", tenantId)
            .claim("roles", roles)
            .claim("project_ids", projects);
        if (scope != null) {
            builder.claim("scope", scope);
        }
        return builder
            .build();
    }
}
