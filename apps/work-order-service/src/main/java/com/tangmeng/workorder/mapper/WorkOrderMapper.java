package com.tangmeng.workorder.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.tangmeng.workorder.domain.WorkOrderEntity;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface WorkOrderMapper extends BaseMapper<WorkOrderEntity> {
}
