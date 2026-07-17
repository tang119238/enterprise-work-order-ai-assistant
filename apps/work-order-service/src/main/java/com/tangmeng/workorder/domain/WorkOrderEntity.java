package com.tangmeng.workorder.domain;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@TableName("work_order")
public class WorkOrderEntity {

    @TableId(value = "work_order_no", type = IdType.INPUT)
    private String workOrderNo;
    private String title;
    private String description;
    private String projectName;
    private String spacePath;
    private String orderType;
    private String priority;
    private String status;
    private String assigneeName;
    private String source;
    private String rootWorkOrderNo;
    private String reworkReason;
    private LocalDateTime createdAt;
    private LocalDateTime dueAt;
    private LocalDateTime completedAt;
}

