package com.tangmeng.workorder.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.tangmeng.workorder.domain.WorkOrderEventEntity;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface WorkOrderEventMapper extends BaseMapper<WorkOrderEventEntity> {
}
