package com.tangmeng.workorder.quality;

import com.tangmeng.workorder.controller.GlobalExceptionHandler;
import com.tangmeng.workorder.security.SecurityConfig;
import com.tangmeng.workorder.security.TenantContext;
import com.tangmeng.workorder.security.TenantContextResolver;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.authentication;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(QualityResultController.class)
@Import({SecurityConfig.class, GlobalExceptionHandler.class})
class QualityResultControllerTest {
    private static final UUID TENANT = UUID.fromString("11111111-1111-1111-1111-111111111111");
    private static final UUID USER = UUID.fromString("00000000-0000-0000-0000-000000009001");
    private static final UUID RESULT = UUID.fromString("44444444-4444-4444-4444-444444444444");
    private static final UUID CASE = UUID.fromString("55555555-5555-5555-5555-555555555555");
    private static final UUID PROPOSAL = UUID.fromString("66666666-6666-6666-6666-666666666666");

    @Autowired private MockMvc mvc;
    @MockitoBean private RectificationService service;
    @MockitoBean private JwtDecoder jwtDecoder;
    @MockitoBean private TenantContextResolver resolver;

    @Test
    void requiresAiServiceRoleAndQualityCallbackScope() throws Exception {
        TenantContext wrongScope = context(Set.of("work-order:read"), Set.of("AI_SERVICE"));
        TenantContext human = context(Set.of("quality:callback"), Set.of("QUALITY_REVIEWER"));

        mvc.perform(post("/internal/quality-results")
                .with(authentication(tenantAuthentication(wrongScope))).with(csrf())
                .header("Idempotency-Key", RESULT.toString())
                .contentType("application/json").content(body("FAIL")))
            .andExpect(status().isForbidden());
        mvc.perform(post("/internal/quality-results")
                .with(authentication(tenantAuthentication(human))).with(csrf())
                .header("Idempotency-Key", RESULT.toString())
                .contentType("application/json").content(body("FAIL")))
            .andExpect(status().isForbidden());

        verify(service, never()).accept(any(), any(), any());
    }

    @Test
    void passesVerifiedTenantBodyAndIdempotencyKeyToService() throws Exception {
        TenantContext context = context(Set.of("quality:callback"), Set.of("AI_SERVICE"));
        when(service.accept(eq(context), any(QualityResultCallback.class), eq(RESULT.toString())))
            .thenReturn(new QualityResultCallbackResponse(
                RESULT, CASE, PROPOSAL, "CREATE_RECTIFICATION", "PROPOSED"));

        mvc.perform(post("/internal/quality-results")
                .with(authentication(tenantAuthentication(context))).with(csrf())
                .header("Idempotency-Key", RESULT.toString())
                .contentType("application/json").content(body("FAIL")))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.result_id").value(RESULT.toString()))
            .andExpect(jsonPath("$.rectification_case_id").value(CASE.toString()))
            .andExpect(jsonPath("$.proposal_id").value(PROPOSAL.toString()))
            .andExpect(jsonPath("$.action_type").value("CREATE_RECTIFICATION"));

        verify(service).accept(eq(context), any(QualityResultCallback.class), eq(RESULT.toString()));
    }

    @Test
    void rejectsMissingIdempotencyKeyBeforeServiceInvocation() throws Exception {
        TenantContext context = context(Set.of("quality:callback"), Set.of("AI_SERVICE"));

        mvc.perform(post("/internal/quality-results")
                .with(authentication(tenantAuthentication(context))).with(csrf())
                .contentType("application/json").content(body("PASS")))
            .andExpect(status().isUnprocessableEntity());

        verify(service, never()).accept(any(), any(), any());
    }

    private static String body(String verdict) {
        return """
            {
              "result_id":"44444444-4444-4444-4444-444444444444",
              "quality_job_id":"33333333-3333-3333-3333-333333333333",
              "tenant_id":"11111111-1111-1111-1111-111111111111",
              "work_order_id":"22222222-2222-2222-2222-222222222222",
              "work_order_version":7,
              "inspection_round":1,
              "verdict":"%s",
              "confidence":0.91,
              "work_order_snapshot":{
                "id":"22222222-2222-2222-2222-222222222222",
                "tenant_id":"11111111-1111-1111-1111-111111111111",
                "version":7,
                "status":"COMPLETED"
              },
              "policy_versions":{},
              "findings":[],
              "provenance":null
            }
            """.formatted(verdict);
    }

    private static TenantContext context(Set<String> scopes, Set<String> roles) {
        return new TenantContext(
            TENANT, USER, "quality-service", roles, Set.of(), scopes,
            "quality-request", "quality-trace"
        );
    }

    private static JwtAuthenticationToken tenantAuthentication(TenantContext context) {
        Jwt jwt = Jwt.withTokenValue("quality-token")
            .header("alg", "none").claim("sub", context.subject()).build();
        List<SimpleGrantedAuthority> authorities = new ArrayList<>();
        context.roles().stream().map(SimpleGrantedAuthority::new).forEach(authorities::add);
        context.scopes().stream()
            .map(scope -> new SimpleGrantedAuthority("SCOPE_" + scope)).forEach(authorities::add);
        JwtAuthenticationToken authentication = new JwtAuthenticationToken(
            jwt, authorities, context.subject());
        authentication.setDetails(context);
        return authentication;
    }
}
