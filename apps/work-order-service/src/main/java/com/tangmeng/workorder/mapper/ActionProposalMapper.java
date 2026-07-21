package com.tangmeng.workorder.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.tangmeng.workorder.domain.ActionProposalEntity;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Result;
import org.apache.ibatis.annotations.Results;
import org.apache.ibatis.annotations.Select;

import java.util.UUID;

@Mapper
public interface ActionProposalMapper extends BaseMapper<ActionProposalEntity> {

    @Select("""
        select id, tenant_id, action_type, target_id, command_payload, before_snapshot,
               after_snapshot, risk_level, status, requested_by, confirmed_by,
               expected_version, expires_at, execution_result, error_code, created_at, updated_at
        from action_proposal
        where tenant_id = #{tenantId} and id = #{id}
        """)
    @Results(id = "actionProposalResult", value = {
        @Result(column = "id", property = "id", typeHandler = UuidTypeHandler.class),
        @Result(column = "tenant_id", property = "tenantId", typeHandler = UuidTypeHandler.class),
        @Result(column = "action_type", property = "actionType"),
        @Result(column = "target_id", property = "targetId", typeHandler = UuidTypeHandler.class),
        @Result(column = "requested_by", property = "requestedBy", typeHandler = UuidTypeHandler.class),
        @Result(column = "confirmed_by", property = "confirmedBy", typeHandler = UuidTypeHandler.class),
        @Result(column = "command_payload", property = "commandPayload", typeHandler = JsonNodeTypeHandler.class),
        @Result(column = "before_snapshot", property = "beforeSnapshot", typeHandler = JsonNodeTypeHandler.class),
        @Result(column = "after_snapshot", property = "afterSnapshot", typeHandler = JsonNodeTypeHandler.class),
        @Result(column = "risk_level", property = "riskLevel"),
        @Result(column = "status", property = "status"),
        @Result(column = "expected_version", property = "expectedVersion"),
        @Result(column = "expires_at", property = "expiresAt"),
        @Result(column = "execution_result", property = "executionResult", typeHandler = JsonNodeTypeHandler.class),
        @Result(column = "error_code", property = "errorCode"),
        @Result(column = "created_at", property = "createdAt"),
        @Result(column = "updated_at", property = "updatedAt")
    })
    ActionProposalEntity selectProposalById(
        @Param("tenantId") UUID tenantId,
        @Param("id") UUID id
    );
}
