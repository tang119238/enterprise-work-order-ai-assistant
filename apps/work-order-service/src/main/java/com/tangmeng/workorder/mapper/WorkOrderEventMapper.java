package com.tangmeng.workorder.mapper;

import com.tangmeng.workorder.domain.WorkOrderEventEntity;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface WorkOrderEventMapper {

    @Insert("""
        insert into work_order_event
          (id,tenant_id,work_order_id,event_type,command_type,before_snapshot,after_snapshot,
           actor_id,request_id,trace_id,created_at)
        values
          (#{id},#{tenantId},#{workOrderId},#{eventType},#{commandType},
           #{beforeSnapshot,typeHandler=com.tangmeng.workorder.mapper.JsonNodeTypeHandler},
           #{afterSnapshot,typeHandler=com.tangmeng.workorder.mapper.JsonNodeTypeHandler},
           #{actorId},#{requestId},#{traceId},#{createdAt})
        """)
    int insert(WorkOrderEventEntity event);
}
