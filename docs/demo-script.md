# 3 分钟演示脚本

## 演示目标

在 3 分钟内让面试官看到四件事：

1. 这是可运行的 Java + AI 应用，不是只有截图或 Prompt。
2. 工单事实来自 Java 工具，制度解释来自 RAG，两者边界清晰。
3. 引用、工具调用、模型与降级状态可以审计。
4. 项目默认离线，且通过统一网关兼容多个国内平台。

## 演示前准备

```bash
docker compose up --build -d
python scripts/smoke_test.py
```

打开两个页面：

- <http://127.0.0.1:8000/docs>
- GitHub 仓库 README 首页

确保终端宽度足以展示 JSON。不要在演示环境中保存或展示任何真实 API Key。

## 0:00–0:30 开场

建议讲法：

> 这是一个企业工单 AI 助手 MVP。我把熟悉的 Java 工单领域能力封装成只读服务，再用 FastAPI 和 LangGraph 编排制度检索与工具调用。项目只用合成数据，默认无需模型密钥，一条 Compose 命令就能启动。

快速展示 README 顶部的能力表和 30 题结果，不展开文件目录。

## 0:30–1:05 制度问答

在 Swagger 执行：

```json
{
  "session_id": "demo-k",
  "message": "再次返工时应该怎样关联根工单？"
}
```

指出：

- `tool_calls` 为空，因为问题不需要访问业务事实。
- `citations.document_id` 是 `rework-policy`。
- `section` 定位到返工链路，`quote` 可在 Markdown 制度中逐字找到。
- 引用由程序从检索结果构建，不让模型自行编造。

## 1:05–1:40 工单事实查询

执行：

```json
{
  "session_id": "demo-w",
  "message": "查询 WO-20260718-001 当前状态"
}
```

指出：

- LangGraph 选择 `work_order` 路径。
- `tool_calls[0].name` 是 `get_work_order`，参数和状态完整记录。
- 状态、优先级、负责人和截止时间来自 Spring Boot DTO。
- 这条回答没有制度引用，也没有让模型猜测当前状态。

## 1:40–2:20 组合问答与返工链路

执行：

```json
{
  "session_id": "demo-c",
  "message": "WO-20260718-008 为什么是返工单，接下来怎么处理？"
}
```

指出：

- `get_rework_chain` 返回根工单 `WO-20260718-007` 和返工单 `WO-20260718-008`。
- AI 回答先列确定性工单事实，再解释返工制度。
- 同一响应包含工具审计、制度引用、端到端时延和实际模型元数据。
- Java 返工查询从根工单或返工单进入都能返回完整链路。

## 2:20–2:45 工程化与国内模型兼容

展示 `docs/provider-configuration.md` 的平台表，建议讲法：

> 默认是 offline，招聘者克隆后就能跑。在线模式有统一环境变量；DeepSeek、百炼、智谱、Kimi、千帆走 OpenAI 兼容适配器，火山方舟单独适配 Responses API。超时和限流做有界重试，失败后是否降级可配置，响应会明确标记 fallback 和 error_code。

强调在线适配器目前完成的是 MockTransport 协议测试，不宣称已用真实付费密钥验证全部平台。

## 2:45–3:00 量化收尾

运行或展示已保存的命令输出：

```bash
python eval/run_eval.py --base-url http://localhost:8000 --output eval/report.json
```

收尾话术：

> 评测集固定为 10 个制度问题、10 个工单查询和 10 个组合问题。本地结果是 30/30 成功，Recall@5、引用有效率、工具准确率和必需事实准确率均为 100%。下一步我会优先增加 pgvector 混合召回、可观测链路和企业认证，而不是先扩大不可控的写操作范围。

## 面试追问准备

### 为什么 AI 服务不直连工单数据库？

业务权限、字段口径、分页和返工关系属于 Java 领域服务。通过只读 API 暴露稳定 DTO，可以复用既有治理能力，也避免 Agent 绕过业务边界。

### 为什么 MVP 不直接使用向量数据库？

知识库只有 12 个制度段落，BM25 已能离线复现并达到评测门槛。项目通过 `PolicyIndex` 隔离检索实现，后续可以增加 pgvector 与重排器并做同集对比。

### 如何防止模型编造工单状态？

工单事实先由 Java 工具读取，再由代码确定性格式化；模型只收到问题和制度片段，结构化引用、工具记录和事实字段均不由模型生成。

### 真实平台不可用怎么办？

网关只重试可恢复错误。启用回退时返回离线答案，并在 `model.fallback` 和 `error_code` 中披露；关闭回退时 API 返回标准化 503。

### 如何证明不是只测 Mock？

Mock 只覆盖平台 HTTP 契约。业务闭环使用真实 Docker Compose：PostgreSQL、Spring Boot、FastAPI 和 30 次 HTTP 请求全部实际运行。文档明确区分两类验证。

## 演示结束后

```bash
docker compose down --volumes
```

不要把 `eval/report.json`、`.env` 或终端中的真实密钥提交到仓库。
