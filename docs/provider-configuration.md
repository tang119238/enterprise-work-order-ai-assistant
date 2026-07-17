# 国内大模型配置指南

## 1. 验证边界

本项目把“协议正确”和“真实平台可用”分开描述：

- **已验证**：离线模式端到端运行；各适配器通过 `httpx.MockTransport` 校验 URL、Authorization、请求体、响应解析、错误映射、重试和降级。
- **未代替验证**：没有把真实付费 API Key 放入测试或仓库，也没有宣称所有平台都已完成真实账号、额度、区域和模型权限联调。

平台可能调整模型名称、区域端点和参数约束。首次使用在线模式时，应以平台控制台实际可用模型为准，并执行本文的真实密钥验证步骤。

## 2. 公共环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LLM_PROVIDER` | `offline` | 平台标识，见下表 |
| `LLM_API_KEY` | 空 | 在线平台的服务端密钥；不要提交 |
| `LLM_BASE_URL` | 空 | 通常使用代码内官方预设；`custom` 必填 |
| `LLM_MODEL` | 空 | 在线模式必填；离线模式保持空 |
| `LLM_TIMEOUT_SECONDS` | `30` | 单次平台请求超时 |
| `LLM_MAX_RETRIES` | `2` | 可恢复错误的最大重试次数 |
| `LLM_FALLBACK_ENABLED` | `true` | 在线失败后是否回退离线回答 |
| `WORK_ORDER_BASE_URL` | `http://127.0.0.1:8080` | 本地开发的 Java 服务地址 |
| `KNOWLEDGE_PATH` | `knowledge/policies` | 合成制度目录 |

`.env.example` 保持 `LLM_PROVIDER=offline`、`LLM_API_KEY=`、`LLM_MODEL=`，因此直接复制也不会误发在线请求。

## 3. 平台矩阵

以下模型仅是截至 2026-07-18 根据官方文档选取的**示例值**，不是项目强制默认值。

| `LLM_PROVIDER` | 适配协议 | 代码内 Base URL | 示例 `LLM_MODEL` | 官方说明 |
| --- | --- | --- | --- | --- |
| `offline` | 本地确定性模板 | 无 | 留空 | 无外部调用 |
| `deepseek` | OpenAI Chat Completions | `https://api.deepseek.com` | `deepseek-v4-flash` | [DeepSeek API](https://api-docs.deepseek.com/) |
| `bailian` | OpenAI Chat Completions | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` | [阿里云百炼兼容模式](https://help.aliyun.com/zh/model-studio/use-bailian-in-langchain) |
| `zhipu` | OpenAI Chat Completions | `https://open.bigmodel.cn/api/paas/v4` | `glm-5.2` | [智谱 HTTP API](https://docs.bigmodel.cn/cn/guide/develop/http/introduction) |
| `kimi` | OpenAI Chat Completions | `https://api.moonshot.cn/v1` | `kimi-k2.6` | [Kimi API 概述](https://platform.kimi.com/docs/api/overview) |
| `qianfan` | OpenAI Chat Completions | `https://qianfan.baidubce.com/v2` | `ernie-4.5-turbo-20260402` | [千帆快速开始](https://cloud.baidu.com/doc/qianfan-docs/s/qm8qxemze) |
| `ark` | Responses API | `https://ark.cn-beijing.volces.com/api/v3` | `<your-endpoint-id>` | [火山方舟 Responses 工具调用](https://www.volcengine.com/docs/82379/1958524) |
| `custom` | OpenAI Chat Completions | 必须配置 | `<your-model-id>` | 由网关提供方定义 |

说明：

- DeepSeek 官方已公告旧的 `deepseek-chat` 与 `deepseek-reasoner` 将于 2026-07-24 停用，因此示例使用 `deepseek-v4-flash`。
- Kimi 官方国内端点是 `.cn`；代码中的默认值和契约测试均锁定该地址。
- 火山方舟通常在控制台创建推理接入点后，把接入点 ID 填入 `LLM_MODEL`，不要把文档里的占位值直接使用。
- 若账号所在区域、专属套餐或企业网关要求不同 URL，可显式覆盖 `LLM_BASE_URL`。

## 4. 启用在线平台

复制环境文件：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，以下以 DeepSeek 为例：

```dotenv
LLM_PROVIDER=deepseek
LLM_API_KEY=<replace-with-your-own-server-key>
LLM_BASE_URL=
LLM_MODEL=deepseek-v4-flash
LLM_TIMEOUT_SECONDS=30
LLM_MAX_RETRIES=2
LLM_FALLBACK_ENABLED=true
```

重建 AI 服务：

```bash
docker compose up --build -d ai-service
```

Compose 会先等待 PostgreSQL 和 Java 服务健康。检查当前装配的平台：

```bash
curl http://127.0.0.1:8000/health
```

预期：

```json
{"status":"ok","provider":"deepseek"}
```

## 5. 真实密钥最小验证

先发送一个制度问题，避免业务工具因素干扰模型联调：

```bash
curl -s http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"live-provider-check","message":"返工链路规则是什么？"}'
```

检查四项：

1. HTTP 状态为 200。
2. `model.provider` 是目标平台，不是 `offline`。
3. `model.fallback` 为 `false`。
4. `citations[].quote` 仍可在本地制度中逐字找到。

随后执行完整评测：

```bash
python eval/run_eval.py --base-url http://localhost:8000 --output eval/report.json
```

在线模型只影响制度解释文本。评测器仍会独立验证引用、工具和必需事实，但外部平台延迟、限流与费用由调用方承担。

## 6. 适配器行为

### 6.1 OpenAI 兼容平台

`deepseek`、`bailian`、`zhipu`、`kimi`、`qianfan` 和 `custom` 使用同一适配器：

```text
POST {LLM_BASE_URL}/chat/completions
Authorization: Bearer <LLM_API_KEY>
```

请求字段：`model`、`messages`、`temperature` 和 `max_tokens`。响应读取 `choices[0].message.content` 及可选的 token usage。

部分新模型可能把 `max_tokens` 标记为兼容字段或对采样参数有限制。若平台返回参数错误，应先根据该模型的官方文档选择兼容模型；确有必要时，再新增平台专用适配器，不在通用适配器中堆叠不可验证的条件分支。

### 6.2 火山方舟

`ark` 使用独立适配器：

```text
POST {LLM_BASE_URL}/responses
Authorization: Bearer <LLM_API_KEY>
```

请求使用 `input` 和 `max_output_tokens`，响应兼容顶层 `output_text` 以及 `output[].content[].text` 两种结构。

### 6.3 自定义网关

```dotenv
LLM_PROVIDER=custom
LLM_API_KEY=<replace-with-your-own-server-key>
LLM_BASE_URL=https://gateway.example.com/v1
LLM_MODEL=<your-model-id>
```

自定义端点必须兼容 OpenAI Chat Completions 的非流式响应。`LLM_BASE_URL` 不应包含 `/chat/completions`，适配器会自动追加。

## 7. 重试与降级

| 错误 | 是否重试 | 默认最终行为 |
| --- | --- | --- |
| 网络超时 | 是 | 重试耗尽后离线降级 |
| HTTP 429 | 是 | 指数退避后离线降级 |
| HTTP 5xx | 是 | 重试耗尽后离线降级 |
| 401/403 | 否 | 立即离线降级 |
| 无效模型或请求参数 | 否 | 立即离线降级 |
| 无法解析平台响应 | 否 | 立即离线降级 |

默认退避为 0.25 秒、0.5 秒，次数受 `LLM_MAX_RETRIES` 限制。降级后的响应示例：

```json
{
  "model": {
    "provider": "offline",
    "name": "deterministic-template",
    "fallback": true,
    "error_code": "PROVIDER_TIMEOUT"
  }
}
```

如业务要求平台异常必须失败：

```dotenv
LLM_FALLBACK_ENABLED=false
```

此时标准化 `ProviderError` 会由 API 映射为 HTTP 503。

## 8. 密钥安全

- `.env` 已被 `.gitignore` 排除；不要使用 `git add -f`。
- 只在服务端注入 API Key，不放进浏览器、截图、演示视频或评测报告。
- CI 不读取仓库 Secrets，也不访问真实模型平台。
- 健康检查只返回 provider 名称，不返回模型密钥或完整配置。
- 适配器不记录 Authorization、Prompt 正文或平台完整响应。
- 密钥疑似泄漏时，先在平台控制台吊销，再清理 Git 历史；仅删除当前文件不够。

## 9. 常见故障

| 表现 | 优先检查 |
| --- | --- |
| `/health` 仍显示 `offline` | API Key 是否为空；Compose 是否已重建 AI 容器 |
| 返回 `fallback=true` | `error_code`、容器日志、账户额度、模型权限、区域端点 |
| 401/403 | 密钥是否属于当前平台和区域，是否包含多余空格 |
| 404 | `LLM_BASE_URL` 是否错误地包含了 `/chat/completions` 或 `/responses` |
| invalid model | 运行平台的 List Models API 或查看控制台可用模型 |
| 参数不支持 | 换用表中兼容示例或实现专用适配器 |
| 超时 | 调高 `LLM_TIMEOUT_SECONDS`，同时检查平台状态和网络出口 |
