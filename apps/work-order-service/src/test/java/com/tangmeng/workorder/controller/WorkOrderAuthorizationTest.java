package com.tangmeng.workorder.controller;

import com.tangmeng.workorder.security.SecurityConfig;
import com.tangmeng.workorder.security.TenantContext;
import com.tangmeng.workorder.security.TenantContextResolver;
import com.tangmeng.workorder.service.WorkOrderNotFoundException;
import com.tangmeng.workorder.service.WorkOrderQueryService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;
import java.util.Set;
import java.util.UUID;

import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(WorkOrderController.class)
@Import({SecurityConfig.class, GlobalExceptionHandler.class})
class WorkOrderAuthorizationTest {

    private static final UUID TENANT = UUID.fromString("11111111-1111-1111-1111-111111111111");
    private static final UUID USER = UUID.fromString("00000000-0000-0000-0000-000000009001");
    private static final UUID PROJECT = UUID.fromString("00000000-0000-0000-0000-000000010001");

    @Autowired
    private MockMvc mvc;

    @MockitoBean
    private JwtDecoder jwtDecoder;

    @MockitoBean
    private TenantContextResolver resolver;

    @MockitoBean
    private WorkOrderQueryService service;

    @Test
    void rejectsMissingTokenWithStable401() throws Exception {
        mvc.perform(get("/api/work-orders"))
            .andExpect(status().isUnauthorized())
            .andExpect(jsonPath("$.code").value("UNAUTHORIZED"))
            .andExpect(jsonPath("$.message").value("Authentication required"));
    }

    @Test
    void rejectsInactiveMembershipWithStable403() throws Exception {
        Jwt jwt = jwt("inactive");
        when(jwtDecoder.decode("inactive")).thenReturn(jwt);
        when(resolver.resolve(jwt)).thenReturn(context(Set.of(), Set.of()));

        mvc.perform(get("/api/work-orders").header("Authorization", "Bearer inactive"))
            .andExpect(status().isForbidden())
            .andExpect(jsonPath("$.code").value("FORBIDDEN"))
            .andExpect(jsonPath("$.message").value("Access denied"));
    }

    @Test
    void hidesInaccessibleOrderWithStable404() throws Exception {
        Jwt jwt = jwt("active");
        when(jwtDecoder.decode("active")).thenReturn(jwt);
        when(resolver.resolve(jwt)).thenReturn(context(Set.of("DISPATCHER"), Set.of(PROJECT)));
        when(service.get("WO-OTHER-TENANT"))
            .thenThrow(new WorkOrderNotFoundException("WO-OTHER-TENANT"));

        mvc.perform(get("/api/work-orders/WO-OTHER-TENANT")
                .header("Authorization", "Bearer active"))
            .andExpect(status().isNotFound())
            .andExpect(jsonPath("$.code").value("WORK_ORDER_NOT_FOUND"));
    }

    private static TenantContext context(Set<String> roles, Set<UUID> projects) {
        return new TenantContext(TENANT, USER, "dispatcher-1", roles, projects,
            Set.of("work-order:read"),
            "request-test", "trace-test");
    }

    private static Jwt jwt(String token) {
        return Jwt.withTokenValue(token)
            .header("alg", "RS256")
            .claim("iss", "https://issuer.example")
            .claim("aud", List.of("work-order-service"))
            .claim("sub", "dispatcher-1")
            .claim("tenant_id", TENANT.toString())
            .build();
    }
}
