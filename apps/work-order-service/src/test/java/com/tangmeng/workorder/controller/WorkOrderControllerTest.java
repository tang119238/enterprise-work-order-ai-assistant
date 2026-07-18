package com.tangmeng.workorder.controller;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.security.TenantContext;
import com.tangmeng.workorder.service.WorkOrderNotFoundException;
import com.tangmeng.workorder.service.WorkOrderQueryService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;
import java.util.Set;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.authentication;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(WorkOrderController.class)
@Import(GlobalExceptionHandler.class)
class WorkOrderControllerTest {

    private static final UUID TENANT = UUID.fromString("11111111-1111-1111-1111-111111111111");
    private static final UUID USER = UUID.fromString("00000000-0000-0000-0000-000000009001");
    private static final UUID PROJECT = UUID.fromString("00000000-0000-0000-0000-000000010001");
    private static final TenantContext CONTEXT = new TenantContext(
        TENANT, USER, "dispatcher-1", Set.of("DISPATCHER"), Set.of(PROJECT),
        Set.of("work-order:read"), "request-test", "trace-test"
    );

    @Autowired
    private MockMvc mvc;

    @MockitoBean
    private WorkOrderQueryService service;

    @Test
    void getsOneOrderUsingVerifiedAuthenticationDetails() throws Exception {
        when(service.get(CONTEXT, "WO-20260718-001")).thenReturn(sampleOrder());

        mvc.perform(get("/api/work-orders/WO-20260718-001")
                .with(authentication(tenantAuthentication())))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.id").value("00000000-0000-0000-0000-000000000001"))
            .andExpect(jsonPath("$.work_order_no").value("WO-20260718-001"))
            .andExpect(jsonPath("$.project_id").value(PROJECT.toString()))
            .andExpect(jsonPath("$.assignee_id").value("00000000-0000-0000-0000-000000009002"))
            .andExpect(jsonPath("$.version").value(3))
            .andExpect(jsonPath("$.assignee_name").value("林晓"));

        verify(service).get(CONTEXT, "WO-20260718-001");
    }

    @Test
    void mapsMissingScopedOrderToStable404() throws Exception {
        when(service.get(CONTEXT, "WO-20260718-999"))
            .thenThrow(new WorkOrderNotFoundException("WO-20260718-999"));

        mvc.perform(get("/api/work-orders/WO-20260718-999")
                .with(authentication(tenantAuthentication())))
            .andExpect(status().isNotFound())
            .andExpect(jsonPath("$.code").value("WORK_ORDER_NOT_FOUND"));
    }

    @Test
    void searchesWithVerifiedContextDespiteForgeableScopeParameters() throws Exception {
        Page<WorkOrderEntity> result = Page.of(1, 20, 1);
        result.setRecords(List.of(sampleOrder()));
        when(service.search(eq(CONTEXT), any(), eq(0), eq(20))).thenReturn(result);

        mvc.perform(get("/api/work-orders")
                .with(authentication(tenantAuthentication()))
                .param("tenant_id", "22222222-2222-2222-2222-222222222222")
                .param("project_ids", "00000000-0000-0000-0000-000000020001")
                .param("status", "PENDING_ACCEPTANCE")
                .param("page", "0")
                .param("size", "20"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.page").value(0))
            .andExpect(jsonPath("$.size").value(20))
            .andExpect(jsonPath("$.total").value(1))
            .andExpect(jsonPath("$.items[0].work_order_no").value("WO-20260718-001"));

        verify(service).search(eq(CONTEXT), any(), eq(0), eq(20));
    }

    @Test
    void returnsScopedReworkChain() throws Exception {
        when(service.reworkChain(CONTEXT, "WO-20260718-008")).thenReturn(List.of(
            WorkOrderEntity.builder().workOrderNo("WO-20260718-007").build(),
            WorkOrderEntity.builder().workOrderNo("WO-20260718-008")
                .rootWorkOrderNo("WO-20260718-007").build()
        ));

        mvc.perform(get("/api/work-orders/WO-20260718-008/rework-chain")
                .with(authentication(tenantAuthentication())))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$[0].work_order_no").value("WO-20260718-007"))
            .andExpect(jsonPath("$[1].root_work_order_no").value("WO-20260718-007"));
    }

    @Test
    void rejectsPageSizeAboveOneHundred() throws Exception {
        mvc.perform(get("/api/work-orders")
                .with(authentication(tenantAuthentication()))
                .param("size", "101"))
            .andExpect(status().isBadRequest())
            .andExpect(jsonPath("$.code").value("INVALID_QUERY_PARAMETER"));
    }

    private static WorkOrderEntity sampleOrder() {
        return WorkOrderEntity.builder()
            .id(UUID.fromString("00000000-0000-0000-0000-000000000001"))
            .tenantId(TENANT)
            .projectId(PROJECT)
            .assigneeId(UUID.fromString("00000000-0000-0000-0000-000000009002"))
            .version(3L)
            .workOrderNo("WO-20260718-001")
            .title("A座照明巡检异常")
            .status("PENDING_ACCEPTANCE")
            .assigneeName("林晓")
            .build();
    }

    private static JwtAuthenticationToken tenantAuthentication() {
        Jwt jwt = Jwt.withTokenValue("active")
            .header("alg", "none")
            .claim("sub", CONTEXT.subject())
            .build();
        JwtAuthenticationToken authentication = new JwtAuthenticationToken(
            jwt, List.of(new SimpleGrantedAuthority("DISPATCHER")), CONTEXT.subject()
        );
        authentication.setDetails(CONTEXT);
        return authentication;
    }
}
