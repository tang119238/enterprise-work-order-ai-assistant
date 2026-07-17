package com.tangmeng.workorder.service;

import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.mapper.WorkOrderMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

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
}

