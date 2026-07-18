package com.tangmeng.workorder.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.mapper.WorkOrderMapper;
import com.tangmeng.workorder.security.TenantContext;
import com.tangmeng.workorder.tenant.TenantTransaction;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.util.List;
import java.util.UUID;

@Service
@RequiredArgsConstructor
public class WorkOrderQueryService {

    private final WorkOrderMapper mapper;
    private final TenantTransaction transactions;

    public WorkOrderEntity get(TenantContext context, String workOrderNo) {
        return transactions.required(context, () -> getScoped(context, workOrderNo));
    }

    public IPage<WorkOrderEntity> search(
        TenantContext context,
        WorkOrderSearchCriteria criteria,
        int page,
        int size
    ) {
        return transactions.required(context, () -> {
            if (context.projectIds().isEmpty()) {
                return emptyPage(page, size);
            }
            LambdaQueryWrapper<WorkOrderEntity> query = scopedQuery(context);
            query.eq(StringUtils.hasText(criteria.status()), WorkOrderEntity::getStatus, criteria.status())
                .eq(StringUtils.hasText(criteria.priority()), WorkOrderEntity::getPriority, criteria.priority())
                .eq(StringUtils.hasText(criteria.projectName()), WorkOrderEntity::getProjectName, criteria.projectName())
                .eq(StringUtils.hasText(criteria.assigneeName()), WorkOrderEntity::getAssigneeName, criteria.assigneeName())
                .ge(criteria.createdFrom() != null, WorkOrderEntity::getCreatedAt, criteria.createdFrom())
                .le(criteria.createdTo() != null, WorkOrderEntity::getCreatedAt, criteria.createdTo())
                .orderByDesc(WorkOrderEntity::getCreatedAt);
            return mapper.selectPage(Page.of(page + 1L, size), query);
        });
    }

    public List<WorkOrderEntity> reworkChain(TenantContext context, String workOrderNo) {
        return transactions.required(context, () -> {
            WorkOrderEntity current = getScoped(context, workOrderNo);
            UUID rootId = current.getRootWorkOrderId() == null
                ? current.getId()
                : current.getRootWorkOrderId();
            WorkOrderEntity root = getScopedById(context, rootId, workOrderNo);
            LambdaQueryWrapper<WorkOrderEntity> query = scopedQuery(context);
            query.and(wrapper -> wrapper
                    .eq(WorkOrderEntity::getId, root.getId())
                    .or()
                    .eq(WorkOrderEntity::getRootWorkOrderId, root.getId()))
                .orderByAsc(WorkOrderEntity::getCreatedAt);
            return mapper.selectList(query);
        });
    }

    private WorkOrderEntity getScoped(TenantContext context, String workOrderNo) {
        if (context.projectIds().isEmpty()) {
            throw new WorkOrderNotFoundException(workOrderNo);
        }
        LambdaQueryWrapper<WorkOrderEntity> query = scopedQuery(context);
        query.eq(WorkOrderEntity::getWorkOrderNo, workOrderNo);
        WorkOrderEntity entity = mapper.selectOne(query);
        if (entity == null) {
            throw new WorkOrderNotFoundException(workOrderNo);
        }
        return entity;
    }

    private WorkOrderEntity getScopedById(
        TenantContext context,
        UUID id,
        String requestedWorkOrderNo
    ) {
        LambdaQueryWrapper<WorkOrderEntity> query = scopedQuery(context);
        query.eq(WorkOrderEntity::getId, id);
        WorkOrderEntity entity = mapper.selectOne(query);
        if (entity == null) {
            throw new WorkOrderNotFoundException(requestedWorkOrderNo);
        }
        return entity;
    }

    private static LambdaQueryWrapper<WorkOrderEntity> scopedQuery(TenantContext context) {
        LambdaQueryWrapper<WorkOrderEntity> query = Wrappers.lambdaQuery();
        query.eq(WorkOrderEntity::getTenantId, context.tenantId())
            .in(WorkOrderEntity::getProjectId, context.projectIds());
        return query;
    }

    private static IPage<WorkOrderEntity> emptyPage(int page, int size) {
        Page<WorkOrderEntity> result = Page.of(page + 1L, size, 0);
        result.setRecords(List.of());
        return result;
    }
}
