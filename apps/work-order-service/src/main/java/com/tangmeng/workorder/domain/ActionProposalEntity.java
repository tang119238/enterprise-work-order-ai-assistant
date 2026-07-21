package com.tangmeng.workorder.domain;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.fasterxml.jackson.databind.JsonNode;
import com.tangmeng.workorder.mapper.JsonNodeTypeHandler;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;
import java.util.UUID;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@TableName("action_proposal")
public class ActionProposalEntity {

    @TableId(value = "id", type = IdType.INPUT)
    private UUID id;
    private UUID tenantId;
    private String actionType;
    private UUID targetId;
    @TableField(typeHandler = JsonNodeTypeHandler.class)
    private JsonNode commandPayload;
    @TableField(typeHandler = JsonNodeTypeHandler.class)
    private JsonNode beforeSnapshot;
    @TableField(typeHandler = JsonNodeTypeHandler.class)
    private JsonNode afterSnapshot;
    private String riskLevel;
    private String status;
    private UUID requestedBy;
    private UUID confirmedBy;
    private Long expectedVersion;
    private LocalDateTime expiresAt;
    @TableField(typeHandler = JsonNodeTypeHandler.class)
    private JsonNode executionResult;
    private String errorCode;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
