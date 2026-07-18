package com.tangmeng.workorder.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.tangmeng.workorder.api.ActionProposalResponse;
import com.tangmeng.workorder.api.WorkOrderExecutionResponse;
import com.tangmeng.workorder.command.ActionNotPermittedException;
import com.tangmeng.workorder.command.ActionProposalService;
import com.tangmeng.workorder.security.TenantContext;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.time.LocalDateTime;
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

@WebMvcTest(ActionProposalController.class)
@Import(GlobalExceptionHandler.class)
class ActionProposalControllerTest {

    private static final UUID TENANT = UUID.fromString("11111111-1111-1111-1111-111111111111");
    private static final UUID USER = UUID.fromString("00000000-0000-0000-0000-000000009001");
    private static final UUID PROJECT = UUID.fromString("00000000-0000-0000-0000-000000010001");
    private static final UUID PROPOSAL = UUID.fromString("00000000-0000-0000-0000-000000009101");
    private static final TenantContext CONTEXT = new TenantContext(
        TENANT, USER, "dispatcher-1", Set.of("DISPATCHER"), Set.of(PROJECT),
        Set.of("work-order:write"), "request-test", "trace-test"
    );

    @Autowired
    private MockMvc mvc;

    @Autowired
    private ObjectMapper objectMapper;

    @MockitoBean
    private ActionProposalService service;

    @Test
    void createsProposalFromVerifiedContextAndReturnsAuthoritativeFields() throws Exception {
        ObjectNode after = objectMapper.createObjectNode()
            .put("work_order_no", "WO-20260718-101")
            .put("status", "PENDING_DISPATCH");
        when(service.create(eq(CONTEXT), any())).thenReturn(new ActionProposalResponse(
            PROPOSAL, "CREATE", null, "MEDIUM", "PENDING_CONFIRMATION",
            com.fasterxml.jackson.databind.node.NullNode.getInstance(), after, 0L,
            LocalDateTime.parse("2026-07-18T10:15:00")
        ));

        mvc.perform(post("/api/action-proposals")
                .with(authentication(tenantAuthentication(CONTEXT)))
                .with(csrf())
                .contentType("application/json")
                .content(createBody()))
            .andExpect(status().isCreated())
            .andExpect(jsonPath("$.id").value(PROPOSAL.toString()))
            .andExpect(jsonPath("$.action_type").value("CREATE"))
            .andExpect(jsonPath("$.risk_level").value("MEDIUM"))
            .andExpect(jsonPath("$.status").value("PENDING_CONFIRMATION"))
            .andExpect(jsonPath("$.before_snapshot").value(org.hamcrest.Matchers.nullValue()))
            .andExpect(jsonPath("$.after_snapshot.work_order_no").value("WO-20260718-101"))
            .andExpect(jsonPath("$.expected_version").value(0))
            .andExpect(jsonPath("$.expires_at").value("2026-07-18T10:15:00"));

        verify(service).create(eq(CONTEXT), any());
    }

    @Test
    void rejectsEveryClientSuppliedAuthorityFieldAsInvalidCommand() throws Exception {
        for (String field : List.of(
            "tenant_id", "before_snapshot", "after_snapshot", "risk_level", "status",
            "expected_version", "requested_by", "requester", "confirmed_by", "confirmer",
            "execution_result", "result", "error_code", "unexpected"
        )) {
            String body = createBody().replaceFirst("\\{", "{\\\"" + field + "\\\":\\\"forged\\\",");
            mvc.perform(post("/api/action-proposals")
                    .with(authentication(tenantAuthentication(CONTEXT)))
                    .with(csrf())
                    .contentType("application/json")
                    .content(body))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("INVALID_COMMAND"));
        }

        verify(service, never()).create(any(), any());
    }

    @Test
    void mapsForbiddenActionToStablePublicError() throws Exception {
        when(service.create(eq(CONTEXT), any())).thenThrow(new ActionNotPermittedException());

        mvc.perform(post("/api/action-proposals")
                .with(authentication(tenantAuthentication(CONTEXT)))
                .with(csrf())
                .contentType("application/json")
                .content(createBody()))
            .andExpect(status().isForbidden())
            .andExpect(jsonPath("$.code").value("ACTION_NOT_PERMITTED"));
    }

    @Test
    void malformedActionParametersReturnInvalidCommand() throws Exception {
        mvc.perform(post("/api/action-proposals")
                .with(authentication(tenantAuthentication(CONTEXT)))
                .with(csrf())
                .contentType("application/json")
                .content("""
                    {"action_type":"CANCEL","target_work_order_no":"WO-1","parameters":"forged"}
                    """))
            .andExpect(status().isUnprocessableEntity())
            .andExpect(jsonPath("$.code").value("INVALID_COMMAND"));
    }

    @Test
    void confirmsOnlyWithMatchingStrictBodyAndNonblankIdempotencyKey() throws Exception {
        WorkOrderExecutionResponse response = new WorkOrderExecutionResponse(
            PROPOSAL, UUID.fromString("00000000-0000-0000-0000-000000000001"),
            "WO-20260718-001", "UPDATE", "PROCESSING", 8L
        );
        when(service.confirm(eq(CONTEXT), eq(PROPOSAL), any(), eq("confirm-key")))
            .thenReturn(response);

        mvc.perform(post("/api/action-proposals/{id}/confirm", PROPOSAL)
                .with(authentication(tenantAuthentication(CONTEXT))).with(csrf())
                .header("Idempotency-Key", "confirm-key")
                .contentType("application/json").content("{\"decision\":\"CONFIRM\"}"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.proposal_id").value(PROPOSAL.toString()))
            .andExpect(jsonPath("$.version").value(8));

        for (String body : List.of(
            "{\"decision\":\"REJECT\"}",
            "{\"decision\":\"CONFIRM\",\"confirmed_by\":\"forged\"}",
            "{}"
        )) {
            mvc.perform(post("/api/action-proposals/{id}/confirm", PROPOSAL)
                    .with(authentication(tenantAuthentication(CONTEXT))).with(csrf())
                    .header("Idempotency-Key", "confirm-key")
                    .contentType("application/json").content(body))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("INVALID_COMMAND"));
        }

        mvc.perform(post("/api/action-proposals/{id}/confirm", PROPOSAL)
                .with(authentication(tenantAuthentication(CONTEXT))).with(csrf())
                .header("Idempotency-Key", " ")
                .contentType("application/json").content("{\"decision\":\"CONFIRM\"}"))
            .andExpect(status().isUnprocessableEntity())
            .andExpect(jsonPath("$.code").value("INVALID_COMMAND"));

        mvc.perform(post("/api/action-proposals/{id}/confirm", PROPOSAL)
                .with(authentication(tenantAuthentication(CONTEXT))).with(csrf())
                .contentType("application/json").content("{\"decision\":\"CONFIRM\"}"))
            .andExpect(status().isUnprocessableEntity())
            .andExpect(jsonPath("$.code").value("INVALID_COMMAND"));
    }

    @Test
    void rejectsOnlyWithMatchingStrictBodyAndRecordsTheHumanDecision() throws Exception {
        when(service.reject(eq(CONTEXT), eq(PROPOSAL), any())).thenReturn(true);

        mvc.perform(post("/api/action-proposals/{id}/reject", PROPOSAL)
                .with(authentication(tenantAuthentication(CONTEXT))).with(csrf())
                .contentType("application/json").content("{\"decision\":\"REJECT\"}"))
            .andExpect(status().isNoContent());

        mvc.perform(post("/api/action-proposals/{id}/reject", PROPOSAL)
                .with(authentication(tenantAuthentication(CONTEXT))).with(csrf())
                .contentType("application/json").content("{\"decision\":\"CONFIRM\"}"))
            .andExpect(status().isUnprocessableEntity())
            .andExpect(jsonPath("$.code").value("INVALID_COMMAND"));
    }

    private static String createBody() {
        return """
            {
              "action_type": "CREATE",
              "parameters": {
                "work_order_no": "WO-20260718-101",
                "title": "Cooling alarm",
                "description": "Inspect cooling loop",
                "project_id": "00000000-0000-0000-0000-000000010001",
                "space_path": "Building A/Floor 2",
                "order_type": "INSPECTION",
                "priority": "HIGH",
                "source": "AI_ASSISTANT",
                "due_at": "2026-07-19T10:00:00"
              }
            }
            """;
    }

    private static JwtAuthenticationToken tenantAuthentication(TenantContext context) {
        Jwt jwt = Jwt.withTokenValue("active")
            .header("alg", "none")
            .claim("sub", context.subject())
            .build();
        JwtAuthenticationToken authentication = new JwtAuthenticationToken(
            jwt,
            context.roles().stream().map(SimpleGrantedAuthority::new).toList(),
            context.subject()
        );
        authentication.setDetails(context);
        return authentication;
    }
}
