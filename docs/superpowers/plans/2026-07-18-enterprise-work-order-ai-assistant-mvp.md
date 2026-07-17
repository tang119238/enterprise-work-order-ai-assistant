# Enterprise Work Order AI Assistant MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and publish a locally runnable, synthetic-data enterprise work-order AI assistant that demonstrates Java read-only APIs, BM25 RAG, LangGraph tool orchestration, and configurable domestic LLM providers.

**Architecture:** A Spring Boot service owns synthetic work-order data in PostgreSQL and exposes read-only HTTP APIs. A FastAPI service owns routing, BM25 retrieval, LangGraph orchestration, work-order tool calls, grounded response assembly, and a provider-neutral LLM gateway with offline, OpenAI-compatible, and Volcengine Ark adapters. Docker Compose starts both services and PostgreSQL; offline mode is the default and requires no model key.

**Tech Stack:** Java 17, Spring Boot 3.4, MyBatis-Plus, Flyway, PostgreSQL 16, Python 3.12, FastAPI, LangGraph, jieba, rank-bm25, httpx, pytest, Docker Compose, GitHub Actions.

---

### Task 1: Repository contract and public-safety baseline

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `LICENSE`
- Create: `README.md`
- Create: `docs/superpowers/specs/2026-07-18-enterprise-work-order-ai-assistant-mvp-design.md`

- [x] **Step 1: Write repository safety rules**

Create `.gitignore` with exact exclusions for IDE state, Java/Python build output, virtual environments, `.env`, logs, evaluation output, and local model credentials:

```gitignore
.idea/
.vscode/
*.iml
**/target/
**/__pycache__/
**/.pytest_cache/
**/.mypy_cache/
**/.ruff_cache/
.venv/
venv/
.env
*.log
eval/report.json
secrets/
```

- [x] **Step 2: Add a key-free configuration contract**

Create `.env.example`:

```dotenv
LLM_PROVIDER=offline
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=
LLM_TIMEOUT_SECONDS=30
LLM_MAX_RETRIES=2
LLM_FALLBACK_ENABLED=true
```

- [x] **Step 3: Add the approved specification and README boundary**

The specification must state that all policies, projects, people, and work orders are synthetic. The README must lead with:

```markdown
# Enterprise Work Order AI Assistant

面向企业工单场景的可审计 RAG + Agent 作品集项目。默认离线运行，不需要大模型密钥。

> 本仓库仅使用合成制度和虚构工单，与任何真实企业、客户或生产系统无关。
```

- [x] **Step 4: Commit the documentation baseline**

Run:

```powershell
git add .gitignore .env.example LICENSE README.md docs/superpowers
git commit -m "docs: define work order AI assistant MVP"
```

Expected: the first commit contains documentation only and `git status --short` is empty.

### Task 2: Java work-order domain and query service

**Files:**
- Create: `apps/work-order-service/pom.xml`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/WorkOrderApplication.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/domain/WorkOrderEntity.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/mapper/WorkOrderMapper.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/service/WorkOrderQueryService.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/service/WorkOrderNotFoundException.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/service/WorkOrderQueryServiceTest.java`

- [x] **Step 1: Write the failing single-order and rework-chain tests**

```java
@ExtendWith(MockitoExtension.class)
class WorkOrderQueryServiceTest {
    @Mock WorkOrderMapper mapper;
    @InjectMocks WorkOrderQueryService service;

    @Test
    void returnsOrderByNumber() {
        WorkOrderEntity entity = WorkOrderEntity.builder()
            .workOrderNo("WO-20260718-001")
            .status("PENDING_ACCEPTANCE")
            .assigneeName("林晓")
            .build();
        when(mapper.selectById("WO-20260718-001")).thenReturn(entity);

        assertThat(service.get("WO-20260718-001")).isSameAs(entity);
    }

    @Test
    void throwsStableExceptionWhenOrderDoesNotExist() {
        when(mapper.selectById("WO-20260718-999")).thenReturn(null);

        assertThatThrownBy(() -> service.get("WO-20260718-999"))
            .isInstanceOf(WorkOrderNotFoundException.class)
            .hasMessageContaining("WO-20260718-999");
    }
}
```

- [x] **Step 2: Run the focused test and verify RED**

Run:

```powershell
$env:JAVA_HOME='C:\Program Files\Zulu\zulu-17'
mvn -f apps/work-order-service/pom.xml -Dtest=WorkOrderQueryServiceTest test
```

Expected: compilation fails because the domain, mapper, and service types do not exist.

- [x] **Step 3: Implement the minimal domain and service**

The service contract must be:

```java
@Service
@RequiredArgsConstructor
public class WorkOrderQueryService {
    private final WorkOrderMapper mapper;

    public WorkOrderEntity get(String workOrderNo) {
        WorkOrderEntity entity = mapper.selectById(workOrderNo);
        if (entity == null) {
            throw new WorkOrderNotFoundException(workOrderNo);
        }
        return entity;
    }
}
```

`WorkOrderEntity` uses `work_order_no` as its string primary key and defines every public field from the design: title, description, project name, space path, type, priority, status, assignee, source, root work-order number, rework reason, created time, due time, and completed time.

- [x] **Step 4: Run the focused test and verify GREEN**

Run the command from Step 2.

Expected: `Tests run: 2, Failures: 0, Errors: 0`.

- [x] **Step 5: Commit the domain increment**

```powershell
git add apps/work-order-service
git commit -m "feat(java): add work order query domain"
```

### Task 3: Java read-only API, filters, migrations, and PostgreSQL integration

**Files:**
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/api/WorkOrderResponse.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/api/PageResponse.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/api/ApiError.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/controller/WorkOrderController.java`
- Create: `apps/work-order-service/src/main/java/com/tangmeng/workorder/controller/GlobalExceptionHandler.java`
- Modify: `apps/work-order-service/src/main/java/com/tangmeng/workorder/service/WorkOrderQueryService.java`
- Create: `apps/work-order-service/src/main/resources/application.yml`
- Create: `apps/work-order-service/src/main/resources/db/migration/V1__create_work_orders.sql`
- Create: `apps/work-order-service/src/main/resources/db/migration/V2__seed_synthetic_work_orders.sql`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/controller/WorkOrderControllerTest.java`
- Test: `apps/work-order-service/src/test/java/com/tangmeng/workorder/integration/WorkOrderPostgresIntegrationTest.java`

- [x] **Step 1: Write failing MockMvc tests for the public contract**

```java
@WebMvcTest(WorkOrderController.class)
@Import(GlobalExceptionHandler.class)
class WorkOrderControllerTest {
    @Autowired MockMvc mvc;
    @MockBean WorkOrderQueryService service;

    @Test
    void getsOneOrder() throws Exception {
        when(service.get("WO-20260718-001")).thenReturn(sampleOrder());
        mvc.perform(get("/api/work-orders/WO-20260718-001"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.work_order_no").value("WO-20260718-001"));
    }

    @Test
    void mapsMissingOrderToStable404() throws Exception {
        when(service.get("WO-20260718-999"))
            .thenThrow(new WorkOrderNotFoundException("WO-20260718-999"));
        mvc.perform(get("/api/work-orders/WO-20260718-999"))
            .andExpect(status().isNotFound())
            .andExpect(jsonPath("$.code").value("WORK_ORDER_NOT_FOUND"));
    }

    private static WorkOrderEntity sampleOrder() {
        return WorkOrderEntity.builder()
            .workOrderNo("WO-20260718-001")
            .title("A座照明巡检异常")
            .status("PENDING_ACCEPTANCE")
            .assigneeName("林晓")
            .build();
    }
}
```

- [x] **Step 2: Verify the controller test is RED**

```powershell
$env:JAVA_HOME='C:\Program Files\Zulu\zulu-17'
mvn -f apps/work-order-service/pom.xml -Dtest=WorkOrderControllerTest test
```

Expected: compilation fails because controller and response types do not exist.

- [x] **Step 3: Implement the three GET endpoints**

Controller signatures must be:

```java
@GetMapping("/{workOrderNo}")
WorkOrderResponse get(@PathVariable String workOrderNo)

@GetMapping
PageResponse<WorkOrderResponse> search(
    @RequestParam(required = false) String status,
    @RequestParam(required = false) String priority,
    @RequestParam(required = false) String projectName,
    @RequestParam(required = false) String assigneeName,
    @RequestParam(required = false) LocalDateTime createdFrom,
    @RequestParam(required = false) LocalDateTime createdTo,
    @RequestParam(defaultValue = "0") @Min(0) int page,
    @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size)

@GetMapping("/{workOrderNo}/rework-chain")
List<WorkOrderResponse> reworkChain(@PathVariable String workOrderNo)
```

The service must build a MyBatis-Plus `LambdaQueryWrapper` only for nonblank filters, page with `Page.of(page + 1, size)`, and query the root order plus rows whose `rootWorkOrderNo` equals the root number.

Configure Jackson with `spring.jackson.property-naming-strategy: SNAKE_CASE` so Java DTO fields match the published JSON contract.

- [x] **Step 4: Add deterministic PostgreSQL migrations**

`V1__create_work_orders.sql` creates the `work_order` table and indexes on status, priority, project, assignee, created time, and root number. `V2__seed_synthetic_work_orders.sql` inserts exactly 50 rows by selecting from `generate_series(1, 50)` and uses `ON CONFLICT (work_order_no) DO NOTHING`; rows 8, 18, 28, 38, and 48 are rework orders linked to the preceding root order.

- [x] **Step 5: Add and run Testcontainers integration coverage**

```java
@Testcontainers(disabledWithoutDocker = true)
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class WorkOrderPostgresIntegrationTest {
    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres =
        new PostgreSQLContainer<>("postgres:16-alpine");

    @Autowired JdbcTemplate jdbcTemplate;

    @Test
    void flywaySeedsExactlyFiftyOrders() {
        Long count = jdbcTemplate.queryForObject("select count(*) from work_order", Long.class);
        assertThat(count).isEqualTo(50L);
    }
}
```

Run:

```powershell
$env:JAVA_HOME='C:\Program Files\Zulu\zulu-17'
mvn -f apps/work-order-service/pom.xml test
```

Expected: all unit tests pass; the PostgreSQL test runs when Docker is available and otherwise reports skipped.

- [x] **Step 6: Commit the Java API**

```powershell
git add apps/work-order-service
git commit -m "feat(java): expose synthetic work order read APIs"
```

### Task 4: Python knowledge ingestion and BM25 retrieval

**Files:**
- Create: `apps/ai-service/pyproject.toml`
- Create: `apps/ai-service/app/__init__.py`
- Create: `apps/ai-service/app/knowledge/models.py`
- Create: `apps/ai-service/app/knowledge/loader.py`
- Create: `apps/ai-service/app/knowledge/bm25.py`
- Create: `knowledge/policies/work-order-lifecycle.md`
- Create: `knowledge/policies/rework-policy.md`
- Create: `knowledge/policies/sla-policy.md`
- Test: `apps/ai-service/tests/knowledge/test_loader.py`
- Test: `apps/ai-service/tests/knowledge/test_bm25.py`

- [ ] **Step 1: Write failing loader and retrieval tests**

```python
def test_loader_keeps_document_and_section_identity(tmp_path: Path) -> None:
    policy = tmp_path / "policy.md"
    policy.write_text(
        "<!-- document_id: rework-policy -->\n# 返工规则\n## 3.2 返工链路\n返工单必须关联根工单。",
        encoding="utf-8",
    )
    chunks = load_policy_directory(tmp_path)
    assert chunks[0].document_id == "rework-policy"
    assert chunks[0].section == "3.2 返工链路"


def test_bm25_returns_rework_policy_for_rework_question() -> None:
    policy_index = BM25PolicyIndex(
        [
            PolicyChunk(
                chunk_id="rework-policy:3.2",
                document_id="rework-policy",
                title="返工规则",
                section="3.2 返工链路",
                text="返工单必须关联根工单，并保留返工原因。",
            )
        ]
    )
    hits = policy_index.search("返工单与原工单如何关联", limit=5)
    assert hits[0].document_id == "rework-policy"
```

- [ ] **Step 2: Verify pytest is RED**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\apps\ai-service[dev]"
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/knowledge -q
```

Expected: import errors because the knowledge package is not implemented.

- [ ] **Step 3: Implement focused Markdown chunking and BM25**

`PolicyChunk` and `SearchHit` are immutable Pydantic models. The loader reads the `document_id` comment, H1 title, and H2/H3 sections; each chunk remains between roughly 200 and 500 Chinese characters where possible. `BM25PolicyIndex` tokenizes with `jieba.lcut`, stores source chunks, returns at most five hits, and never fabricates citations.

- [ ] **Step 4: Add three explicitly synthetic policy documents**

Each policy begins with this notice and a stable document ID:

```markdown
> 合成演示制度：本文件与任何真实企业无关。
```

Cover lifecycle, rework-chain handling, SLA/priority, acceptance, and closure rules without using real company names.

- [ ] **Step 5: Run tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/knowledge -q
git add apps/ai-service knowledge
git commit -m "feat(ai): add synthetic policy BM25 retrieval"
```

Expected: knowledge tests pass.

### Task 5: Provider-neutral domestic LLM gateway

**Files:**
- Create: `apps/ai-service/app/config.py`
- Create: `apps/ai-service/app/llm/contracts.py`
- Create: `apps/ai-service/app/llm/errors.py`
- Create: `apps/ai-service/app/llm/offline.py`
- Create: `apps/ai-service/app/llm/openai_compatible.py`
- Create: `apps/ai-service/app/llm/ark.py`
- Create: `apps/ai-service/app/llm/registry.py`
- Create: `apps/ai-service/app/llm/gateway.py`
- Test: `apps/ai-service/tests/llm/test_registry.py`
- Test: `apps/ai-service/tests/llm/test_openai_compatible.py`
- Test: `apps/ai-service/tests/llm/test_ark.py`
- Test: `apps/ai-service/tests/llm/test_gateway.py`

- [ ] **Step 1: Write the cross-provider contract tests**

```python
@pytest.mark.asyncio
async def test_offline_provider_returns_fallback_text() -> None:
    result = await OfflineTemplateProvider().generate(
        LLMRequest(messages=(LLMMessage(role="user", content="问题"),), fallback_text="可信答案")
    )
    assert result.content == "可信答案"
    assert result.provider == "offline"


@pytest.mark.parametrize("name", ["deepseek", "bailian", "zhipu", "kimi", "qianfan"])
def test_domestic_presets_use_openai_compatible_provider(name: str) -> None:
    provider = build_provider(settings_for(name))
    assert isinstance(provider, OpenAICompatibleProvider)


def test_ark_uses_dedicated_responses_adapter() -> None:
    assert isinstance(build_provider(settings_for("ark")), ArkResponsesProvider)


def settings_for(provider: str) -> Settings:
    return Settings(
        llm_provider=provider,
        llm_api_key="test-key",
        llm_base_url="https://example.invalid/v1",
        llm_model="test-model",
    )
```

- [ ] **Step 2: Verify provider tests are RED**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/llm -q
```

Expected: import errors for the absent LLM modules.

- [ ] **Step 3: Implement the common contracts and three providers**

Use these stable contracts:

```python
@dataclass(frozen=True)
class LLMRequest:
    messages: tuple[LLMMessage, ...]
    fallback_text: str
    temperature: float = 0.1
    max_tokens: int = 800


@dataclass(frozen=True)
class LLMResult:
    content: str
    provider: str
    model: str
    latency_ms: int
    fallback: bool = False
    error_code: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
```

The compatible adapter posts to `<base_url>/chat/completions`; the Ark adapter posts to `<base_url>/responses`. Both use an injected `httpx.AsyncClient`, bearer authorization, explicit timeout, and safe response parsing. Neither logs request headers or message bodies.

- [ ] **Step 4: Implement retries and deterministic fallback**

`LLMGateway.generate()` retries timeout, 429, and 5xx failures at most `LLM_MAX_RETRIES`, never retries authentication or invalid-model errors, and returns the offline result with `fallback=True` and a safe `error_code` when fallback is enabled. Inject the sleep coroutine so tests complete without real delays.

- [ ] **Step 5: Run provider tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/llm -q
git add apps/ai-service/app/config.py apps/ai-service/app/llm apps/ai-service/tests/llm
git commit -m "feat(ai): add domestic model provider gateway"
```

Expected: all adapter, registry, retry, fallback, and secret-redaction tests pass without live API keys.

### Task 6: Work-order tools, LangGraph orchestration, and FastAPI contract

**Files:**
- Create: `apps/ai-service/app/api/models.py`
- Create: `apps/ai-service/app/tools/work_order_client.py`
- Create: `apps/ai-service/app/agent/state.py`
- Create: `apps/ai-service/app/agent/router.py`
- Create: `apps/ai-service/app/agent/composer.py`
- Create: `apps/ai-service/app/agent/graph.py`
- Create: `apps/ai-service/app/main.py`
- Test: `apps/ai-service/tests/tools/test_work_order_client.py`
- Test: `apps/ai-service/tests/agent/test_router.py`
- Test: `apps/ai-service/tests/agent/test_graph.py`
- Test: `apps/ai-service/tests/api/test_chat.py`

- [ ] **Step 1: Write failing tests for all three routes**

```python
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("返工单创建后如何处理？", "knowledge"),
        ("查询 WO-20260718-001 当前状态", "work_order"),
        ("WO-20260718-008 为什么是返工单，接下来怎么处理？", "combined"),
    ],
)
def test_routes(message: str, expected: str) -> None:
    assert route_intent(message) == expected
```

```python
def test_chat_contract(client: TestClient) -> None:
    response = client.post("/chat", json={"session_id": "demo-001", "message": "返工规则是什么？"})
    assert response.status_code == 200
    body = response.json()
    assert set(["answer", "citations", "tool_calls", "latency_ms", "model"]) <= body.keys()
```

- [ ] **Step 2: Verify agent/API tests are RED**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests/agent apps/ai-service/tests/api apps/ai-service/tests/tools -q
```

Expected: import errors for router, graph, tools, and application factory.

- [ ] **Step 3: Implement auditable work-order tools**

`WorkOrderClient` exposes `get_work_order`, `search_work_orders`, and `get_rework_chain`. It maps 404 to `WORK_ORDER_NOT_FOUND`, timeout/connection errors to `WORK_ORDER_SERVICE_UNAVAILABLE`, and returns only the public DTO fields. Each graph tool record contains name, arguments, and `success` or `error`; it does not include full descriptions in logs.

- [ ] **Step 4: Implement the LangGraph state machine**

The compiled graph follows exact routes:

```text
START -> normalize_input -> route_intent
knowledge -> retrieve_knowledge -> compose_answer
work_order -> call_work_order_tool -> compose_answer
combined -> call_work_order_tool -> retrieve_knowledge -> compose_answer
compose_answer -> validate_grounding -> build_response -> END
```

Structured work-order facts are formatted deterministically before any model call. Model output may explain retrieved policy but may not provide citations or tool-call metadata; those are built from actual chunks and calls.

- [ ] **Step 5: Implement `/chat` and `/health`**

Pydantic response fields are exactly `answer`, `citations`, `tool_calls`, `latency_ms`, `model`, and `warnings`. Reject blank messages and messages above 2,000 characters with HTTP 422. `/health` reports service status and configured provider without exposing secrets.

- [ ] **Step 6: Run agent/API tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests -q
git add apps/ai-service
git commit -m "feat(ai): orchestrate grounded work order chat"
```

Expected: all Python tests pass in offline mode.

### Task 7: Docker Compose, evaluation, CI, and smoke tests

**Files:**
- Create: `apps/work-order-service/Dockerfile`
- Create: `apps/ai-service/Dockerfile`
- Create: `docker-compose.yml`
- Create: `eval/questions.json`
- Create: `eval/run_eval.py`
- Create: `scripts/smoke_test.py`
- Create: `.github/workflows/ci.yml`
- Test: `apps/ai-service/tests/eval/test_evaluator.py`

- [ ] **Step 1: Write a failing evaluator unit test**

```python
def test_evaluator_calculates_required_metrics() -> None:
    result = evaluate_case(
        expected_document_ids={"rework-policy"},
        expected_tools={"get_rework_chain"},
        response={
            "citations": [
                {
                    "document_id": "rework-policy",
                    "quote": "返工单必须关联根工单。",
                }
            ],
            "tool_calls": [{"name": "get_rework_chain", "status": "success"}],
            "answer": "返工单必须关联根工单。",
        },
        policy_texts={"rework-policy": "返工单必须关联根工单。"},
    )
    assert result.retrieval_hit is True
    assert result.citations_valid is True
    assert result.tools_correct is True
```

- [ ] **Step 2: Implement the 30-case offline evaluation set**

`eval/questions.json` contains exactly 10 knowledge, 10 work-order, and 10 combined cases. Each case specifies `message`, `expected_document_ids`, `expected_tools`, `required_facts`, and `forbidden_facts`. The evaluator calculates retrieval Recall@5, citation validity, tool accuracy, and successful-request rate.

- [ ] **Step 3: Add production-like container builds**

The Java Dockerfile uses a Maven/Temurin 17 build stage and a Temurin 17 JRE runtime. The Python Dockerfile uses `python:3.12-slim`, installs the package, copies only application and knowledge files, and runs uvicorn as a non-root user.

`docker-compose.yml` starts `postgres`, `work-order-service`, and `ai-service`, adds health checks, uses internal service URLs, and exposes only Java `8080` and AI `8000`. The default Provider remains `offline`.

- [ ] **Step 4: Run the complete local acceptance path**

```powershell
docker compose up --build -d
python scripts/smoke_test.py
python eval/run_eval.py --base-url http://localhost:8000 --output eval/report.json
docker compose down
```

Expected:

```text
smoke tests: PASS
successful requests: 30/30
retrieval Recall@5: >= 0.80
citation validity: >= 0.90
tool accuracy: 1.00
```

- [ ] **Step 5: Add CI and commit**

GitHub Actions must run Java tests, Python lint/type/tests, Docker builds, Compose smoke tests, the offline evaluator, and a repository secret/path scan. It must not use repository secrets or call live model platforms.

```powershell
git add docker-compose.yml apps/*/Dockerfile eval scripts .github
git commit -m "ci: verify one-command offline MVP"
```

### Task 8: Public documentation, final verification, and GitHub publication

**Files:**
- Modify: `README.md`
- Create: `docs/architecture.md`
- Create: `docs/demo-script.md`
- Create: `docs/provider-configuration.md`

- [ ] **Step 1: Complete the recruiter-facing README**

Document prerequisites, one-command startup, three copyable `curl` requests, architecture, synthetic-data boundary, provider configuration, evaluation results, troubleshooting, and a concise technology decision section. Clearly distinguish Mock contract verification from real-key verification.

- [ ] **Step 2: Add provider examples without secrets**

Provide configuration tables for `offline`, `deepseek`, `bailian`, `zhipu`, `kimi`, `qianfan`, `ark`, and `custom`. Use clearly labelled example model names in documentation, but keep `LLM_MODEL` blank in executable `.env.example` so offline startup always works.

- [ ] **Step 3: Run the complete verification suite**

```powershell
$env:JAVA_HOME='C:\Program Files\Zulu\zulu-17'
mvn -f apps/work-order-service/pom.xml test
.\.venv\Scripts\python.exe -m ruff check apps/ai-service
.\.venv\Scripts\python.exe -m mypy apps/ai-service/app
.\.venv\Scripts\python.exe -m pytest apps/ai-service/tests -q
git diff --check
git grep -n -I -E "(sk-[A-Za-z0-9_-]{16,}|Bearer [A-Za-z0-9._-]{20,}|C:\\\\Users\\\\)" -- .
```

Expected: all test/lint/type commands pass, `git diff --check` is silent, and the sensitive-content scan returns no matches outside the internal plan exclusion.

- [ ] **Step 4: Commit the verified release candidate**

```powershell
git add README.md docs
git commit -m "docs: add recruiter demo and provider guide"
git status --short
```

Expected: clean worktree.

- [ ] **Step 5: Create and push the public GitHub repository**

When GitHub CLI is available and authenticated:

```powershell
gh repo create enterprise-work-order-ai-assistant --public --source . --remote origin --push --description "Auditable enterprise work-order RAG and Agent MVP with Java, LangGraph, and domestic LLM adapters"
```

If repository creation uses the signed-in GitHub web UI instead, add its HTTPS URL as `origin`, then run `git push -u origin main`. Confirm success only from the remote URL and push output.

- [ ] **Step 6: Verify the published repository**

Run:

```powershell
git remote -v
git status --short --branch
git ls-remote --heads origin main
```

Expected: `origin` points to the new public repository, the local branch tracks `origin/main`, the worktree is clean, and `refs/heads/main` exists remotely.
