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
@TableName(value = "work_order_event", autoResultMap = true)
public class WorkOrderEventEntity {
    @TableId(value = "id", type = IdType.INPUT)
    private UUID id;
    private UUID tenantId;
    private UUID workOrderId;
    private String eventType;
    private String commandType;
    @TableField(typeHandler = JsonNodeTypeHandler.class)
    private JsonNode beforeSnapshot;
    @TableField(typeHandler = JsonNodeTypeHandler.class)
    private JsonNode afterSnapshot;
    private UUID actorId;
    private String requestId;
    private String traceId;
    private LocalDateTime createdAt;
}
