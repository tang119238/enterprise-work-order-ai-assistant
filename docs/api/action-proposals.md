# 操作建议 API

本页描述当前已实现的多租户工单写入契约。Java 服务是工单事实的唯一写入者；调用方、Agent 和 `AI_SERVICE` 只能先创建建议，不能把客户端计算的预览直接写入事实表。

## 1. 先看这个：decision 是严格契约

阶段 API 对请求体执行未知字段拒绝，以下两组端点和请求体必须精确匹配：

```http
POST /api/action-proposals/{proposal-id}/confirm
Idempotency-Key: <nonblank-key-at-most-200-characters>
Content-Type: application/json

{"decision":"CONFIRM"}
```

```http
POST /api/action-proposals/{proposal-id}/reject
Content-Type: application/json

{"decision":"REJECT"}
```

`confirm` 使用 `REJECT`、`reject` 使用 `CONFIRM`、小写 decision、`{}`、缺少 `Idempotency-Key`，或增加 `confirmed_by`、`tenant_id` 等任意字段，都不会被宽松接受，而是返回 `422 INVALID_COMMAND`。`reject` 成功返回 204；当前实现不要求拒绝请求携带幂等键。

## 2. 本地 JWT 与安全密钥配置

所有 `/api/**` 和 `/internal/**` 都要求 Bearer JWT；只有 `/actuator/health` 公开。JWT 必须通过 RS256 签名、`iss`、`aud`、`exp` 和 `nbf` 校验，并包含以下业务声明：

```json
{
  "iss": "http://localhost:9000",
  "sub": "synthetic-dispatcher-a",
  "aud": ["work-order-service"],
  "exp": 1784336400,
  "nbf": 1784332800,
  "tenant_id": "11111111-1111-1111-1111-111111111111",
  "roles": ["DISPATCHER"],
  "project_ids": ["00000000-0000-0000-0000-000000010001"],
  "scope": "work-order:read work-order:write",
  "request_id": "synthetic-request-001",
  "trace_id": "synthetic-trace-001"
}
```

`sub` 是签发器内稳定主体，不是显示名；`tenant_id` 是当前租户；`roles` 与 `project_ids` 必须是数组；`scope` 可为空格分隔字符串或字符串数组；`request_id`/`trace_id` 可省略，省略时服务生成合成 UUID。示例时间只展示字段形状，实际 Token 必须使用当前有效的短期时间窗。

权限不是只信 Token：

```text
有效角色 = JWT roles ∩ 当前 ACTIVE tenant_membership
有效项目 = JWT project_ids ∩ 当前 ACTIVE project_scope
```

数据库中已撤销的角色/项目不会因旧 Token 仍未过期而继续生效。跨租户、跨项目与不存在统一表现为 404。Token 对应的 `user_identity`、成员关系和项目范围必须在执行前以合成 fixture 明确建立；基础种子只保证两个租户、六个项目和 50 条工单，不隐式授予任何人权限。

应用读取以下安全配置：

```dotenv
JWT_ISSUER_URI=http://localhost:9000
JWT_AUDIENCE=work-order-service
JWT_JWK_SET_URI=
JWT_PUBLIC_KEY_LOCATION=classpath:security/dev-jwt-public-key.pem
```

- 有 JWK 服务时设置 `JWT_JWK_SET_URI`，它优先于 PEM 公钥。
- 使用 PEM 时只挂载/打包公钥，并将 `JWT_PUBLIC_KEY_LOCATION` 设置为 Spring `Resource` 地址，例如 `file:/run/secrets/work-order-jwt-public.pem`。
- JWT 私钥只留在测试签发器、HSM 或 Secret Store；不要写入 `.env`、Compose、日志、Token 示例或仓库。
- 仓库内 classpath 公钥只用于合成开发身份，不能成为生产信任根。
- 基础 Compose 使用 classpath 开发公钥。若要覆盖 JWT 环境变量，应通过部署配置或不提交的 Compose override 注入，并用 `docker compose config` 检查最终值；仅修改 `.env` 而未把变量传入容器并不会改变服务配置。

冒烟测试还会在发请求前对头部、RS256 签名、issuer、audience、主体、角色、scope、租户/项目 UUID、`nbf`/`exp` 和最长 15 分钟寿命做 fail-closed 预检；Spring Security 会独立完成服务端验签和声明校验。所需三枚 Token：

| 环境变量 | 合成身份要求 |
| --- | --- |
| `SMOKE_DISPATCHER_TOKEN` | 租户 A，`DISPATCHER`，项目 A |
| `SMOKE_TENANT_B_TOKEN` | 租户 B，`DISPATCHER`，项目 B |
| `SMOKE_AI_TOKEN` | 与 dispatcher Token 相同 `sub`，租户 A，同时包含 `AI_SERVICE` 和 `DISPATCHER`，项目 A |

三枚 Token 字符串必须不同且未过期；dispatcher 与 AI Token 使用同一合成主体，数据库必须同时赋予该主体 `DISPATCHER` 和 `AI_SERVICE`。这样 dispatcher Token 的成功写入与 AI 多角色 Token 的 403 共同证明“有效多角色仍禁止 AI 决策”，而不是只测试 Token 中一个未落库的装饰性角色。数据库当前授权必须与声明匹配。

## 3. 租户、项目、角色与命令

每个建议的创建和决策都会检查租户、项目和当前数据库权限。下表是当前实现的精确矩阵，不把尚未实现的管理员权限算作可用命令：

| 有效角色 | 可创建的建议 | 可确认/拒绝 | 附加约束 |
| --- | --- | --- | --- |
| `DISPATCHER` | `CREATE`、`ASSIGN`、`UPDATE`、`CANCEL` | 同四类 | 目标项目必须在有效项目交集中 |
| `OPERATOR` | `ACCEPT`、`START`、`COMPLETE` | 同三类 | 当前 `user_identity.id` 必须是工单负责人 |
| `QUALITY_REVIEWER` | `CLOSE` | `CLOSE` | 工单必须处于状态机允许关闭的状态 |
| `AI_SERVICE` | 全部八类建议 | 无；确认和拒绝都为 403 | 即使同时有任意人类角色也禁止决策 |
| `TENANT_ADMIN` | 本阶段无工单命令 | 本阶段无工单命令 | 预留给租户配置/审计，不隐式继承其他角色 |

创建建议时的角色与确认时的角色都要满足；确认会重新读取数据库授权。状态机为：

```text
PENDING_DISPATCH --ASSIGN--> PENDING_ACCEPTANCE
PENDING_ACCEPTANCE --ACCEPT--> PENDING_ACCEPTANCE (记录 accepted_at)
PENDING_ACCEPTANCE --START--> PROCESSING
PROCESSING --COMPLETE--> COMPLETED
COMPLETED --CLOSE--> CLOSED
PENDING_DISPATCH | PENDING_ACCEPTANCE | PROCESSING | COMPLETED --CANCEL--> CANCELLED
```

`CLOSED`/`CANCELLED` 不可修改；取消必须有非空原因。

## 4. 创建建议与权威预览

```http
POST /api/action-proposals
Authorization: Bearer <jwt>
Content-Type: application/json
```

外层只允许 `action_type`、可选 `target_work_order_no` 和 `parameters`。服务器拒绝客户端提供的 `tenant_id`、`before_snapshot`、`after_snapshot`、`risk_level`、`status`、`expected_version`、requester/confirmer、`execution_result`、`result`、`error_code` 或其他未知字段。

| 动作 | `target_work_order_no` | `parameters` 的精确字段 | 风险 |
| --- | --- | --- | --- |
| `CREATE` | 必须省略 | 必填 `work_order_no,title,description,project_id,space_path,order_type,priority,source,due_at` | MEDIUM |
| `ASSIGN` | 必填 | 必填 `assignee_id,assignee_name,reason` | MEDIUM |
| `UPDATE` | 必填 | `title,description,priority,due_at` 至少一个 | LOW |
| `ACCEPT` | 必填 | `{}` | LOW |
| `START` | 必填 | `{}` | MEDIUM |
| `COMPLETE` | 必填 | `{}` | HIGH |
| `CLOSE` | 必填 | `{}` | HIGH |
| `CANCEL` | 必填 | 必填 `reason` | HIGH |

`due_at` 使用无时区 ISO LocalDateTime，例如 `2026-07-19T10:00:00`。建议成功返回 HTTP 201：

```json
{
  "id": "30000000-0000-0000-0000-000000000001",
  "action_type": "CREATE",
  "risk_level": "MEDIUM",
  "status": "PENDING_CONFIRMATION",
  "before_snapshot": null,
  "after_snapshot": {
    "id": "40000000-0000-0000-0000-000000000001",
    "tenant_id": "11111111-1111-1111-1111-111111111111",
    "work_order_no": "SYNTH-WO-0001",
    "status": "PENDING_DISPATCH",
    "version": 0
  },
  "expected_version": 0,
  "expires_at": "2026-07-18T10:15:00"
}
```

服务器在租户事务中重读项目/工单并计算 before/after、风险、版本、请求人和 15 分钟过期时间。`after_snapshot` 是需要人审核的权威预览，不是已发生的事实；只有确认成功后的 `work_order` 与事件才是事实。

## 5. 生命周期、幂等、并发与乐观锁

```text
PENDING_CONFIRMATION --human reject--> REJECTED
PENDING_CONFIRMATION --expired--> EXPIRED
PENDING_CONFIRMATION --atomic human claim--> EXECUTING --transaction commit--> EXECUTED
                                                       --execution failure--> FAILED
```

数据库兼容 `CONFIRMED` 中间状态，但当前 HTTP 确认路径会原子抢占 `PENDING_CONFIRMATION` 为 `EXECUTING`，不会向客户端暴露可操作的自主确认阶段。

确认事务的顺序是：幂等读取/保留 → 重读当前授权与目标 → 原子抢占建议 → `tenant_id + id + version` 写工单 → Assignment（负责人变化时）→ `work_order_event` → `outbox_event` → 保存幂等响应 → `EXECUTED`。任一步失败都回滚；不会留下“工单已变但审计没写”的半事务。

- 幂等唯一键为 `(tenant_id, CONFIRM_ACTION_PROPOSAL, Idempotency-Key)`。
- 相同键、相同 proposal/decision 返回保存的 HTTP 200 JSON；不再次修改版本，不追加事件或 Outbox。
- 相同键用于不同 proposal，返回 `409 IDEMPOTENCY_KEY_CONFLICT`。
- 每个非 CREATE 成功命令把 `version` 精确加 1；CREATE 的初始 `version` 为 0。
- 预览的 `expected_version` 与事实不一致，或并发更新只允许一个版本谓词成功；失败返回 `409 WORK_ORDER_VERSION_CONFLICT` 和服务器重新计算的 `fresh_preview`，不会自动覆盖。
- 并发还受建议原子抢占和数据库唯一约束保护，不能靠客户端重试规避。

## 6. 可复制 PowerShell 示例

先把短期合成 Token 放入当前进程环境；不要把它们写入文档或提交：

```powershell
$base = 'http://127.0.0.1:8080'
$env:DISPATCHER_TOKEN = '<short-lived-synthetic-dispatcher-jwt>'
$auth = @{ Authorization = "Bearer $env:DISPATCHER_TOKEN" }
```

### CREATE 建议

```powershell
$createBody = @{
  action_type = 'CREATE'
  parameters = @{
    work_order_no = 'SYNTH-WO-0001'
    title = 'Synthetic cooling inspection'
    description = 'Inspect the synthetic cooling loop'
    project_id = '00000000-0000-0000-0000-000000010001'
    space_path = 'Synthetic/Building-A/Floor-2'
    order_type = 'INSPECTION'
    priority = 'HIGH'
    source = 'LOCAL_TEST'
    due_at = '2026-07-19T10:00:00'
  }
} | ConvertTo-Json -Depth 5 -Compress

$proposal = Invoke-RestMethod -Method Post `
  -Uri "$base/api/action-proposals" `
  -Headers $auth -ContentType 'application/json' -Body $createBody
$proposal | ConvertTo-Json -Depth 10
```

### CONFIRM 与同键 replay

```powershell
$confirmHeaders = @{
  Authorization = "Bearer $env:DISPATCHER_TOKEN"
  'Idempotency-Key' = 'synthetic-create-confirm-0001'
}
$confirmBody = '{"decision":"CONFIRM"}'

$first = Invoke-RestMethod -Method Post `
  -Uri "$base/api/action-proposals/$($proposal.id)/confirm" `
  -Headers $confirmHeaders -ContentType 'application/json' -Body $confirmBody
$replay = Invoke-RestMethod -Method Post `
  -Uri "$base/api/action-proposals/$($proposal.id)/confirm" `
  -Headers $confirmHeaders -ContentType 'application/json' -Body $confirmBody

if (($first | ConvertTo-Json -Compress) -ne ($replay | ConvertTo-Json -Compress)) {
  throw 'Idempotent replay changed the response'
}
```

### REJECT 另一条建议

使用未确认且未过期的新建议；以下只演示决策调用：

```powershell
$rejectProposalId = '<pending-synthetic-proposal-uuid>'
Invoke-RestMethod -Method Post `
  -Uri "$base/api/action-proposals/$rejectProposalId/reject" `
  -Headers $auth -ContentType 'application/json' `
  -Body '{"decision":"REJECT"}'
```

成功为 HTTP 204，无响应 JSON。已执行的建议不能再拒绝。

## 7. 稳定 HTTP/错误码

| HTTP | `code` | 当前场景 |
| ---: | --- | --- |
| 400 | `INVALID_QUERY_PARAMETER` | 查询分页/日期等参数无效 |
| 401 | `AUTHENTICATION_REQUIRED` | Bearer Token 缺失，或签名、issuer、audience、时间无效 |
| 403 | `FORBIDDEN` | 已认证但没有任何受保护 API authority（Security Filter） |
| 403 | `ACTION_NOT_PERMITTED` | 当前角色不能创建/决策，或 Token/数据库有效角色含 `AI_SERVICE` 却尝试决策 |
| 404 | `WORK_ORDER_NOT_FOUND` | 工单/建议不存在，或超出租户/项目范围 |
| 409 | `WORK_ORDER_VERSION_CONFLICT` | 乐观锁冲突；响应含 `fresh_preview` |
| 409 | `IDEMPOTENCY_KEY_CONFLICT` | 同一租户同一幂等键绑定不同确认操作 |
| 409 | `INVALID_STATE_TRANSITION` | 当前工单状态不允许动作 |
| 410 | `ACTION_PROPOSAL_EXPIRED` | 建议超过 15 分钟有效期 |
| 422 | `INVALID_COMMAND` | 动作参数、decision、幂等键或未知字段无效 |
| 500 | `INTERNAL_ERROR` | 未公开的内部异常，不泄露栈或数据库信息 |

错误 JSON 形状为 `code`、`message`、`timestamp`；版本冲突另含 `fresh_preview`。

## 8. Docker/运行时与验收边界

本地运行需要 Java 17、已安装 Maven、Python 3.12+、Docker Engine/Desktop、Compose v2，以及首次构建所需的镜像网络访问。Compose 必须真实启动 PostgreSQL；不能用静态 Mock 代替 RLS、事务、事件和 Outbox 验收。

```powershell
$env:JAVA_HOME = '<path-to-jdk-17>'
mvn -f apps/work-order-service/pom.xml test
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests scripts/tests -q
.\.venv\Scripts\python.exe -m py_compile scripts/smoke_test.py scripts/generate_smoke_fixtures.py
docker version
docker compose version
.\.venv\Scripts\python.exe -m pip install -e "apps/ai-service[dev]"
docker compose -f docker-compose.yml build
.\.venv\Scripts\python.exe scripts/generate_smoke_fixtures.py --output .smoke
docker compose --env-file .smoke/smoke.env -f docker-compose.yml -f docker-compose.smoke.yml config --quiet
docker compose --env-file .smoke/smoke.env -f docker-compose.yml -f docker-compose.smoke.yml up -d
Get-Content -Raw .smoke/provision.sql | docker compose --env-file .smoke/smoke.env -f docker-compose.yml -f docker-compose.smoke.yml exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d workorders
.\.venv\Scripts\python.exe scripts/smoke_test.py --env-file .smoke/smoke.env
docker compose --env-file .smoke/smoke.env -f docker-compose.yml -f docker-compose.smoke.yml down
git diff --check
```

`generate_smoke_fixtures.py` 依赖 dev extra 中的 `cryptography`。应先完成可能耗时的镜像下载/构建，再生成凭据；它每次生成新的 2048-bit RSA 私钥和默认精确 900 秒 Token，只写入 Git 忽略的 `.smoke/`。`nbf` 保留 5 秒时钟偏移，`exp` 按 `nbf + lifetime` 计算，因此精确满足最长 900 秒边界。`docker-compose.smoke.yml` 只把公钥只读挂载给 Java。生成的 SQL 仅由本地管理员用于幂等建立合成 `user_identity`、ACTIVE 成员和项目范围，并在每个租户事务内执行 `SET LOCAL`；私钥和 Token 不进入 SQL。不要把 `.smoke/` 复制到提交或日志中。

脚本使用唯一 `SMOKE-<12-hex>` 工单，不清理既有数据。建议创建后，它先通过工单 API 确认 404，再使用受限运行时角色 `work_order_app`（不是 `postgres`）在 `BEGIN; SET LOCAL app.tenant_id=...; ...; COMMIT;` 中按 tenant、工单号、proposal id 和权威 `after_snapshot.id` 精确计数；事件与 Outbox 直接按该预览工单 UUID 过滤，要求事实表/事件/Outbox 为零而建议为一。数据库密码只通过子进程环境转发，不进入命令 argv。CREATE 后要求 `(version,event,outbox)=(0,1,1)`，UPDATE 后为 `(1,2,2)`，同键 replay 后仍不变。知识问答 `/chat` 也会单独验证引用；工单/组合 `/chat` 仍因 `WorkOrderClient` 不转发 Token 而不属于本阶段 live smoke。

纯 Java/Python 测试通过只说明可执行契约通过；只有 Docker 可用且上述 live smoke 打印 `smoke tests: PASS`，才能声明 PostgreSQL RLS/Compose 端到端验收通过。若 Docker 不可用，必须把 Compose、Docker 门控 Testcontainers 和 live smoke 明确记录为 skipped/blocked，不能写成通过。

2026-07-18 当前工作树的实际验证：

| 套件 | 通过 | 跳过 | 失败/错误 |
| --- | ---: | ---: | ---: |
| Java 全量 | 145 | 23 | 0 |
| Python AI 服务 | 43 | 0 | 0 |
| Python scripts | 11 | 0 | 0 |

23 个 Java 跳过全部由不可用的 Docker/Testcontainers 门控：`ActionProposalMapperIntegrationTest` 1、`IdempotencyConcurrencyTest` 12、`TenantRlsIntegrationTest` 3、`TenantSchemaIntegrationTest` 5、`WorkOrderPostgresIntegrationTest` 2。Ruff、Python 编译和双 Compose 文件的 `config --quiet` 通过。Docker 客户端 29.6.1、Compose 5.2.0 可用，但 Linux Engine named pipe 不存在；因此 `docker compose up`、管理员 fixture 导入、PostgreSQL RLS 和 live smoke 均为 blocked/not run，不声明端到端通过。Compose 配置解析成功与 live smoke 是两个独立门槛，不能互相替代。
