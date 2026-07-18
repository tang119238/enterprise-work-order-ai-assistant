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
@TableName("project")
public class ProjectEntity {
    @TableId(value = "id", type = IdType.INPUT)
    private UUID id;
    private UUID tenantId;
    private String projectKey;
    private String name;
    private String status;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
