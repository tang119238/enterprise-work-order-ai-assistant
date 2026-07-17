package com.tangmeng.workorder.controller;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.service.WorkOrderNotFoundException;
import com.tangmeng.workorder.service.WorkOrderQueryService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(WorkOrderController.class)
@Import(GlobalExceptionHandler.class)
class WorkOrderControllerTest {

    @Autowired
    private MockMvc mvc;

    @MockitoBean
    private WorkOrderQueryService service;

    @Test
    void getsOneOrder() throws Exception {
        when(service.get("WO-20260718-001")).thenReturn(sampleOrder());

        mvc.perform(get("/api/work-orders/WO-20260718-001"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.work_order_no").value("WO-20260718-001"))
            .andExpect(jsonPath("$.assignee_name").value("林晓"));
    }

    @Test
    void mapsMissingOrderToStable404() throws Exception {
        when(service.get("WO-20260718-999"))
            .thenThrow(new WorkOrderNotFoundException("WO-20260718-999"));

        mvc.perform(get("/api/work-orders/WO-20260718-999"))
            .andExpect(status().isNotFound())
            .andExpect(jsonPath("$.code").value("WORK_ORDER_NOT_FOUND"));
    }

    @Test
    void searchesOrdersWithZeroBasedPublicPaging() throws Exception {
        Page<WorkOrderEntity> result = Page.of(1, 20, 1);
        result.setRecords(List.of(sampleOrder()));
        when(service.search(any(), eq(0), eq(20))).thenReturn(result);

        mvc.perform(get("/api/work-orders")
                .param("status", "PENDING_ACCEPTANCE")
                .param("page", "0")
                .param("size", "20"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.page").value(0))
            .andExpect(jsonPath("$.size").value(20))
            .andExpect(jsonPath("$.total").value(1))
            .andExpect(jsonPath("$.items[0].work_order_no").value("WO-20260718-001"));
    }

    @Test
    void returnsReworkChain() throws Exception {
        when(service.reworkChain("WO-20260718-008")).thenReturn(List.of(
            WorkOrderEntity.builder().workOrderNo("WO-20260718-007").build(),
            WorkOrderEntity.builder().workOrderNo("WO-20260718-008")
                .rootWorkOrderNo("WO-20260718-007").build()
        ));

        mvc.perform(get("/api/work-orders/WO-20260718-008/rework-chain"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$[0].work_order_no").value("WO-20260718-007"))
            .andExpect(jsonPath("$[1].root_work_order_no").value("WO-20260718-007"));
    }

    @Test
    void rejectsPageSizeAboveOneHundred() throws Exception {
        mvc.perform(get("/api/work-orders").param("size", "101"))
            .andExpect(status().isBadRequest())
            .andExpect(jsonPath("$.code").value("INVALID_QUERY_PARAMETER"));
    }

    private static WorkOrderEntity sampleOrder() {
        return WorkOrderEntity.builder()
            .workOrderNo("WO-20260718-001")
            .title("A座照明巡检异常")
            .status("PENDING_ACCEPTANCE")
            .assigneeName("林晓")
            .build();
    }
}
