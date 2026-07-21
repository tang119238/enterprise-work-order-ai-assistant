package com.tangmeng.workorder.controller;

import com.tangmeng.workorder.security.TenantContext;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.authentication;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(UnrelatedBodyController.class)
@Import(GlobalExceptionHandler.class)
class GlobalExceptionHandlerTest {

    private static final TenantContext CONTEXT = new TenantContext(
        UUID.fromString("11111111-1111-1111-1111-111111111111"),
        UUID.fromString("00000000-0000-0000-0000-000000009001"),
        "dispatcher", Set.of("DISPATCHER"),
        Set.of(UUID.fromString("00000000-0000-0000-0000-000000010001")),
        Set.of("work-order:write"), "request", "trace"
    );

    @Autowired
    private MockMvc mvc;

    @Test
    void unrelatedMalformedBodyKeepsGenericServerErrorContract() throws Exception {
        mvc.perform(post("/api/unrelated-body")
                .with(authentication(tenantAuthentication()))
                .with(csrf())
                .contentType("application/json")
                .content("{"))
            .andExpect(status().isInternalServerError())
            .andExpect(jsonPath("$.code").value("INTERNAL_ERROR"));
    }

    private static JwtAuthenticationToken tenantAuthentication() {
        Jwt jwt = Jwt.withTokenValue("active").header("alg", "none")
            .claim("sub", CONTEXT.subject()).build();
        JwtAuthenticationToken authentication = new JwtAuthenticationToken(
            jwt, List.of(new SimpleGrantedAuthority("DISPATCHER")), CONTEXT.subject()
        );
        authentication.setDetails(CONTEXT);
        return authentication;
    }

}

@RestController
@RequestMapping("/api/unrelated-body")
class UnrelatedBodyController {
    @PostMapping
    Map<String, String> parse(@RequestBody Map<String, String> body) {
        return body;
    }
}
