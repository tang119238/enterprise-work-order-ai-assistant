# Enterprise Work Order AI Assistant MVP Design

**Status:** Approved
**Date:** 2026-07-18
**Data boundary:** Synthetic demonstration content only

## Goal

Build a public, recruiter-runnable portfolio project that combines enterprise Java backend engineering with RAG, LangGraph tool orchestration, and configurable domestic model providers. A new user must be able to start the project with `docker compose up --build`; offline mode must work without an API key.

## Scope

The MVP supports three paths:

1. Policy questions answered from synthetic Markdown policies with exact citations.
2. Work-order queries backed by a read-only Spring Boot API and synthetic PostgreSQL data.
3. Combined questions that call the work-order API and then explain the result with retrieved policy evidence.

The MVP does not include write operations, authentication, a management UI, OCR, vector storage, NL2SQL, streaming, provider-native function calling, or real customer material.

## Architecture

```text
Client -> FastAPI /chat -> LangGraph
                           |-> BM25 policy retrieval
                           |-> Spring Boot read tools -> PostgreSQL
                           `-> LLM gateway
                               |-> offline templates
                               |-> OpenAI-compatible providers
                               `-> Volcengine Ark Responses adapter
```

The Java service owns work-order data and querying. The Python service owns orchestration, retrieval, provider adapters, grounding, and the public chat contract. Services communicate only over HTTP.

## Java service

The Spring Boot service uses Java 17, MyBatis-Plus, Flyway, and PostgreSQL 16. It exposes:

```http
GET /api/work-orders/{workOrderNo}
GET /api/work-orders
GET /api/work-orders/{workOrderNo}/rework-chain
GET /actuator/health
```

The list endpoint accepts status, priority, project, assignee, creation-time range, page, and size. Page numbering starts at zero, the default size is 20, and the maximum size is 100. The database contains exactly 50 deterministic synthetic work orders, including five rework chains.

## AI service

The FastAPI service exposes:

```http
POST /chat
GET /health
```

`POST /chat` returns `answer`, `citations`, `tool_calls`, `latency_ms`, `model`, and `warnings`. LangGraph uses fixed nodes for normalization, intent routing, retrieval, tool execution, composition, grounding validation, and response construction.

Work-order numbers, statuses, assignees, deadlines, and rework links are rendered from tool output by deterministic code. A model may explain retrieved policy but does not create citations, tool-call records, or structured work-order facts.

## Retrieval and citations

Synthetic policies are Markdown files with stable document and section identifiers. The service tokenizes Chinese text with jieba and uses BM25 Top-5 retrieval. A citation is valid only when its document, section, and quoted text can be located in the source policy. With insufficient evidence, the assistant explicitly says the knowledge base has no basis for a conclusion.

## Model gateway

All orchestration code depends on one `LLMProvider` protocol. Implementations are:

- `OfflineTemplateProvider` for deterministic, key-free operation.
- `OpenAICompatibleProvider` for DeepSeek, Alibaba Bailian, Zhipu, Kimi, Baidu Qianfan, and custom compatible endpoints.
- `ArkResponsesProvider` for Volcengine Ark and Doubao Responses-style APIs.

Runtime selection uses:

```dotenv
LLM_PROVIDER=offline
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=
LLM_TIMEOUT_SECONDS=30
LLM_MAX_RETRIES=2
LLM_FALLBACK_ENABLED=true
```

Timeouts, HTTP 429, and server errors retry at most twice and then fall back offline. Authentication, invalid-model, and malformed-response errors do not retry. Fallback is visible in response metadata; secrets and message bodies are not logged.

## Safety

- No real company names, policies, employee names, customer data, source code, chat history, local absolute paths, or credentials.
- Only GET operations exist in the Java API and tool registry.
- `.env` and generated logs are ignored; `.env.example` contains no usable credential.
- CI scans for token patterns and local paths before publication.

## Testing and acceptance

The repository contains Java unit/controller/PostgreSQL integration tests, Python retrieval/provider/agent/API tests, Mock provider contract tests, Docker smoke tests, and 30 offline evaluation questions.

Acceptance thresholds:

- 30/30 offline requests complete without 5xx.
- Retrieval Recall@5 is at least 80%.
- Citation validity is at least 90%.
- Tool selection and key arguments are correct for all 10 tool-query cases.
- A clean machine can start the system with one Docker Compose command.
- No secret, local path, or real business material is tracked.
