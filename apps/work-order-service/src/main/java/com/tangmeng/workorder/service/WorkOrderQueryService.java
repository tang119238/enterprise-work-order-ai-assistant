package com.tangmeng.workorder.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.mapper.WorkOrderMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.util.List;

@Service
@RequiredArgsConstructor
public class WorkOrderQueryService {

    private final WorkOrderMapper mapper;

    public WorkOrderEntity get(String workOrderNo) {
        WorkOrderEntity entity = mapper.selectById(workOrderNo);
        if (entity == null) {
            throw new WorkOrderNotFoundException(workOrderNo);
        }
        return entity;
    }

    public IPage<WorkOrderEntity> search(WorkOrderSearchCriteria criteria, int page, int size) {
        LambdaQueryWrapper<WorkOrderEntity> query = Wrappers.lambdaQuery();
        query.eq(StringUtils.hasText(criteria.status()), WorkOrderEntity::getStatus, criteria.status())
            .eq(StringUtils.hasText(criteria.priority()), WorkOrderEntity::getPriority, criteria.priority())
            .eq(StringUtils.hasText(criteria.projectName()), WorkOrderEntity::getProjectName, criteria.projectName())
            .eq(StringUtils.hasText(criteria.assigneeName()), WorkOrderEntity::getAssigneeName, criteria.assigneeName())
            .ge(criteria.createdFrom() != null, WorkOrderEntity::getCreatedAt, criteria.createdFrom())
            .le(criteria.createdTo() != null, WorkOrderEntity::getCreatedAt, criteria.createdTo())
            .orderByDesc(WorkOrderEntity::getCreatedAt);
        return mapper.selectPage(Page.of(page + 1L, size), query);
    }

    public List<WorkOrderEntity> reworkChain(String workOrderNo) {
        WorkOrderEntity current = get(workOrderNo);
        String rootWorkOrderNo = StringUtils.hasText(current.getRootWorkOrderNo())
            ? current.getRootWorkOrderNo()
            : current.getWorkOrderNo();
        LambdaQueryWrapper<WorkOrderEntity> query = Wrappers.lambdaQuery();
        query.and(wrapper -> wrapper
                .eq(WorkOrderEntity::getWorkOrderNo, rootWorkOrderNo)
                .or()
                .eq(WorkOrderEntity::getRootWorkOrderNo, rootWorkOrderNo))
            .orderByAsc(WorkOrderEntity::getCreatedAt);
        return mapper.selectList(query);
    }
}

