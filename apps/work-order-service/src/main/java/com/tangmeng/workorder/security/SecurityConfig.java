package com.tangmeng.workorder.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.tangmeng.workorder.api.ApiError;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.convert.converter.Converter;
import org.springframework.core.io.Resource;
import org.springframework.http.MediaType;
import org.springframework.security.authentication.AbstractAuthenticationToken;
import org.springframework.security.authorization.AuthorizationManagers;
import org.springframework.security.authorization.AuthorityAuthorizationManager;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.converter.RsaKeyConverters;
import org.springframework.security.oauth2.core.DelegatingOAuth2TokenValidator;
import org.springframework.security.oauth2.core.OAuth2TokenValidator;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtClaimValidator;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtValidators;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.security.web.SecurityFilterChain;

import java.io.IOException;
import java.io.InputStream;
import java.security.interfaces.RSAPublicKey;
import java.util.ArrayList;
import java.util.List;

@Configuration
public class SecurityConfig {

    private static final String[] TENANT_ROLES = {
        "TENANT_ADMIN", "DISPATCHER", "OPERATOR", "QUALITY_REVIEWER", "AI_SERVICE"
    };

    @Bean
    SecurityFilterChain securityFilterChain(
        HttpSecurity http,
        TenantContextResolver resolver,
        ObjectMapper objectMapper
    ) throws Exception {
        http
            .csrf(csrf -> csrf.disable())
            .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
            .authorizeHttpRequests(authorize -> authorize
                .requestMatchers("/actuator/health", "/actuator/health/**").permitAll()
                .requestMatchers("/internal/quality-events/**")
                    .access(AuthorizationManagers.allOf(
                        AuthorityAuthorizationManager.hasAuthority("SCOPE_quality:consume"),
                        AuthorityAuthorizationManager.hasAuthority("AI_SERVICE")
                    ))
                .requestMatchers("/internal/quality-results/**")
                    .access(AuthorizationManagers.allOf(
                        AuthorityAuthorizationManager.hasAuthority("SCOPE_quality:callback"),
                        AuthorityAuthorizationManager.hasAuthority("AI_SERVICE")
                    ))
                .requestMatchers("/api/**", "/internal/**").hasAnyAuthority(TENANT_ROLES)
                .anyRequest().denyAll())
            .oauth2ResourceServer(resourceServer -> resourceServer
                .jwt(jwt -> jwt.jwtAuthenticationConverter(jwtAuthenticationConverter(resolver)))
                .authenticationEntryPoint((request, response, exception) ->
                    writeError(response, objectMapper, HttpServletResponse.SC_UNAUTHORIZED,
                        "AUTHENTICATION_REQUIRED", "Authentication required"))
                .accessDeniedHandler((request, response, exception) ->
                    writeError(response, objectMapper, HttpServletResponse.SC_FORBIDDEN,
                        "FORBIDDEN", "Access denied")));
        return http.build();
    }

    @Bean
    @ConditionalOnMissingBean(JwtDecoder.class)
    JwtDecoder jwtDecoder(
        @Value("${security.jwt.issuer-uri}") String issuer,
        @Value("${security.jwt.audience}") String audience,
        @Value("${security.jwt.jwk-set-uri:}") String jwkSetUri,
        @Value("${security.jwt.public-key-location}") Resource publicKeyResource
    ) {
        NimbusJwtDecoder decoder = jwkSetUri == null || jwkSetUri.isBlank()
            ? publicKeyDecoder(publicKeyResource)
            : NimbusJwtDecoder.withJwkSetUri(jwkSetUri).build();
        decoder.setJwtValidator(jwtValidator(issuer, audience));
        return decoder;
    }

    static Converter<Jwt, AbstractAuthenticationToken> jwtAuthenticationConverter(
        TenantContextResolver resolver
    ) {
        return token -> {
            TenantContext context = resolver.resolve(token);
            List<SimpleGrantedAuthority> authorities = new ArrayList<>();
            context.roles().stream()
                .map(SimpleGrantedAuthority::new)
                .forEach(authorities::add);
            context.scopes().stream()
                .map(scope -> new SimpleGrantedAuthority("SCOPE_" + scope))
                .forEach(authorities::add);
            JwtAuthenticationToken authentication = new JwtAuthenticationToken(
                token, authorities, context.subject()
            );
            authentication.setDetails(context);
            return authentication;
        };
    }

    static OAuth2TokenValidator<Jwt> jwtValidator(String issuer, String audience) {
        OAuth2TokenValidator<Jwt> issuerValidator = JwtValidators.createDefaultWithIssuer(issuer);
        OAuth2TokenValidator<Jwt> audienceValidator = new JwtClaimValidator<List<String>>(
            "aud", audiences -> audiences != null && audiences.contains(audience)
        );
        return new DelegatingOAuth2TokenValidator<>(issuerValidator, audienceValidator);
    }

    private static NimbusJwtDecoder publicKeyDecoder(Resource publicKeyResource) {
        try (InputStream input = publicKeyResource.getInputStream()) {
            RSAPublicKey publicKey = RsaKeyConverters.x509().convert(input);
            if (publicKey == null) {
                throw new IllegalStateException("JWT public key is empty");
            }
            return NimbusJwtDecoder.withPublicKey(publicKey).build();
        } catch (IOException exception) {
            throw new IllegalStateException("Could not load JWT public key", exception);
        }
    }

    private static void writeError(
        HttpServletResponse response,
        ObjectMapper objectMapper,
        int status,
        String code,
        String message
    ) throws IOException {
        response.setStatus(status);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        objectMapper.writeValue(response.getOutputStream(), ApiError.of(code, message));
    }
}
