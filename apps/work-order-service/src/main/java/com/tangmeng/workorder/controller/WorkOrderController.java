package com.tangmeng.workorder.controller;

import com.tangmeng.workorder.api.PageResponse;
import com.tangmeng.workorder.api.WorkOrderResponse;
import com.tangmeng.workorder.security.TenantContext;
import com.tangmeng.workorder.service.WorkOrderQueryService;
import com.tangmeng.workorder.service.WorkOrderSearchCriteria;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import lombok.RequiredArgsConstructor;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.security.core.Authentication;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDateTime;
import java.util.List;

@RestController
@RequestMapping("/api/work-orders")
@RequiredArgsConstructor
@Validated
public class WorkOrderController {

    private final WorkOrderQueryService service;

    @GetMapping("/{workOrderNo}")
    public WorkOrderResponse get(Authentication authentication, @PathVariable String workOrderNo) {
        return WorkOrderResponse.from(service.get(tenantContext(authentication), workOrderNo));
    }

    @GetMapping
    public PageResponse<WorkOrderResponse> search(
        Authentication authentication,
        @RequestParam(required = false) String status,
        @RequestParam(required = false) String priority,
        @RequestParam(required = false) String projectName,
        @RequestParam(required = false) String assigneeName,
        @RequestParam(required = false)
        @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime createdFrom,
        @RequestParam(required = false)
        @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime createdTo,
        @RequestParam(defaultValue = "0") @Min(0) int page,
        @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size
    ) {
        WorkOrderSearchCriteria criteria = new WorkOrderSearchCriteria(
            status, priority, projectName, assigneeName, createdFrom, createdTo
        );
        return PageResponse.from(
            service.search(tenantContext(authentication), criteria, page, size),
            WorkOrderResponse::from
        );
    }

    @GetMapping("/{workOrderNo}/rework-chain")
    public List<WorkOrderResponse> reworkChain(
        Authentication authentication,
        @PathVariable String workOrderNo
    ) {
        return service.reworkChain(tenantContext(authentication), workOrderNo).stream()
            .map(WorkOrderResponse::from)
            .toList();
    }

    private static TenantContext tenantContext(Authentication authentication) {
        if (authentication != null && authentication.getDetails() instanceof TenantContext context) {
            return context;
        }
        throw new IllegalStateException("Verified tenant context is missing");
    }
}
