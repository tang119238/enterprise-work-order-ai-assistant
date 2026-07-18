package com.tangmeng.workorder.domain;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
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
@TableName("work_order")
public class WorkOrderEntity {

    @TableId(value = "id", type = IdType.INPUT)
    private UUID id;
    private UUID tenantId;
    private String workOrderNo;
    private String title;
    private String description;
    private UUID projectId;
    private String projectName;
    private String spacePath;
    private String orderType;
    private String priority;
    private String status;
    private UUID assigneeId;
    private String assigneeName;
    private String source;
    private UUID rootWorkOrderId;
    private String rootWorkOrderNo;
    private String reworkReason;
    private long version;
    private LocalDateTime acceptedAt;
    private UUID createdBy;
    private UUID updatedBy;
    private LocalDateTime createdAt;
    private LocalDateTime dueAt;
    private LocalDateTime completedAt;
    private LocalDateTime cancelledAt;
    private String cancelReason;
}
