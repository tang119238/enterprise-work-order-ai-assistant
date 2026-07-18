# AI 工单质检与整改闭环设计

**状态：** 已完成会话设计确认，等待书面规格复核
**日期：** 2026-07-18

## 1. 目标与范围

在工单完成后自动创建可重试、可审计的质检任务，结合确定性规则、制度检索和模型结构化判断生成结果；不通过或不确定时生成整改建议，经质检人员确认后创建关联整改单，并支持多轮复检。

AI 判断不是最终工单事实。模型不能直接关闭工单、创建整改单或覆盖人工复核。

## 2. 所有权与边界

- Java：产生工单完成事件，拥有整改案例、整改单和最终状态；
- Python：拥有质检任务、结果、发现、模型调用和补偿；
- 混合检索：提供制度依据；
- 模型：只返回结构化判断；
- `QUALITY_REVIEWER`：确认整改、复检结论和关闭。

## 3. 数据模型

### `quality_job`

保存 `tenant_id`、`work_order_id`、`work_order_version`、`business_key`、`trigger_source`、`status`、`priority`、`retry_count`、`max_retry_count`、`next_retry_at`、开始/结束时间、最近错误和结果 ID。

业务键为 `(tenant_id, work_order_id, work_order_version, inspection_round)`，确保同一完成版本和轮次只有一个任务。

状态为 `PENDING`、`RUNNING`、`RETRY_WAIT`、`SUCCEEDED`、`FAILED`、`SKIPPED`。处理器通过条件更新从 `PENDING/RETRY_WAIT` 抢占为 `RUNNING`。

### `quality_result`

每轮只追加一条不可变结果，保存 `verdict`、`confidence`、工单快照、制度版本、输入附件摘要、模型调用 ID、生成时间和是否已回调 Java。判定为 `PASS`、`FAIL`、`UNCERTAIN`、`SKIP`。

### `quality_finding`

每个问题保存 `rule_code`、`severity`、`label`、`evidence`、`policy_chunk_id`、`recommendation`、`confidence` 和 `source=RULE|MODEL`。

### `model_call_audit`

保存提供方、模型、Prompt 版本、请求 ID、耗时、Token、费用、输入/响应摘要、截断原始响应和错误。不得保存完整图片 URL、联系方式或推理链。

### `rectification_case`

由 Java 保存原工单、当前质检结果、整改单、轮次和状态。状态为 `PROPOSED`、`RECTIFYING`、`RECHECKING`、`CLOSED`。

## 4. 处理流程

1. Java 在工单进入 `COMPLETED` 的事务中写 Outbox；
2. Python 以业务键幂等创建任务并快速确认接收；
3. 组装工单事实、附件摘要和当前制度命中；
4. 先执行 SLA、必填字段、时间范围和附件存在性等确定性规则；
5. 将必要快照和制度片段发送给模型；
6. 按 JSON Schema 解析并拒绝未知字段、未知规则和越界置信度；
7. 规则结果与模型结果聚合并保存不可变 result/findings；
8. 回调 Java 保存质检事实和操作建议；
9. `PASS` 生成关闭建议，`FAIL/UNCERTAIN` 生成整改建议；
10. 质检人员确认整改建议后，Java 创建关联整改单；
11. 整改单完成后创建下一轮任务；复检通过关闭案例，不通过继续同一案例。

## 5. 模型输出契约

```json
{
  "verdict": "PASS|FAIL|UNCERTAIN|SKIP",
  "confidence": 0.0,
  "findings": [
    {
      "rule_code": "POLICY_RULE_CODE",
      "severity": "LOW|MEDIUM|HIGH",
      "label": "PASS|FAIL|UNCERTAIN|SKIP",
      "evidence": "可核验证据",
      "policy_chunk_id": "命中的分块 ID",
      "recommendation": "整改建议",
      "confidence": 0.0
    }
  ]
}
```

`policy_chunk_id` 必须属于本次检索命中；不满足时该 finding 进入 `UNCERTAIN`，不能伪造依据。确定性失败规则不能被模型改写为通过。

## 6. 重试和补偿

- 第 n 次失败后等待 `min(5 * 2^(n-1), 60)` 分钟；
- 默认最多 3 次模型调用；
- 超过阈值进入 `FAILED` 并产生告警；
- 任务补偿只处理任务状态，回调补偿只处理未回调结果；
- 结果以 `quality_job_id` 唯一，重复插入复用已存在结果；
- 模型超时、429 和 5xx 可重试，认证、Schema 错误和输入缺失不盲目重试；
- 无附件时生成 `SKIP` 结果，不调用模型。

## 7. 人工覆盖

人工复核可以接受、驳回或修改建议，但必须保存原 AI 结果、人工结论、原因、操作人和时间。人工覆盖不修改旧结果，只追加复核事件。

## 8. 测试与验收

- 任务业务键幂等、CAS 抢占、重试退避和补偿测试；
- 规则结果优先级、模型 Schema、依据校验和聚合测试；
- 模型成功、超时、429、认证失败、坏 JSON 和无附件测试；
- 回调重复投递只生成一个整改建议；
- 未确认整改建议不创建工单；
- 多轮复检保留全部结果，关闭案例只能发生在最新一轮通过后；
- 两租户任务、结果、发现和模型日志完全隔离；
- 质检结果证据、模型和 Prompt 版本可追溯率为 100%。
