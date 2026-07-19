from __future__ import annotations

import json
from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest

from app.knowledge.models import RetrievalHit, RetrievalResult
from app.llm.contracts import StructuredLLMRequest, StructuredLLMResult
from app.llm.errors import (
    ProviderAuthError,
    ProviderBadResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.quality.models import ClaimedQualityJob, QualityResultRecord
from app.quality.processor import QualityOutputError, QualityProcessor, policy_rule_code

TENANT = UUID("11111111-1111-1111-1111-111111111111")
WORK_ORDER = UUID("22222222-2222-2222-2222-222222222222")
JOB = UUID("33333333-3333-3333-3333-333333333333")
CHUNK = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1")


def _job(*, attachments: bool = True, summary: str = "Repair verified") -> ClaimedQualityJob:
    attachment_summary: tuple[dict[str, Any], ...] = ()
    if attachments:
        attachment_summary = (
            {
                "attachment_id": "proof-1",
                "media_type": "image/jpeg",
                "size_bytes": 2048,
                "present": True,
            },
        )
    return ClaimedQualityJob(
        id=JOB,
        tenant_id=TENANT,
        work_order_id=WORK_ORDER,
        work_order_version=7,
        inspection_round=1,
        retry_count=0,
        work_order_snapshot={
            "id": str(WORK_ORDER),
            "tenant_id": str(TENANT),
            "version": 7,
            "status": "COMPLETED",
            "title": "Air handler repair",
            "description": "Synthetic unit stopped",
            "order_type": "REPAIR",
            "priority": "HIGH",
            "space_path": "HQ/F2",
            "completion_summary": summary,
            "created_at": "2026-07-20T01:00:00",
            "due_at": "2026-07-20T03:00:00",
            "completed_at": "2026-07-20T02:00:00",
            "contact_phone": "must-not-enter-prompt",
        },
        attachments_summary=attachment_summary,
    )


def _hit() -> RetrievalHit:
    return RetrievalHit(
        chunk_id=str(CHUNK),
        document_id="completion-policy",
        document_key="completion-policy",
        title="Completion evidence policy",
        section="3.2",
        text="Completed repairs must have evidence.",
        ordinal=0,
        document_version=4,
        content_hash="a" * 64,
        bm25_rank=1,
        vector_rank=1,
        rrf_score=0.032,
    )


def _model_payload(*, chunk_id: UUID | None = CHUNK) -> dict[str, object]:
    return {
        "verdict": "PASS",
        "confidence": 0.93,
        "findings": [
            {
                "rule_code": policy_rule_code(CHUNK),
                "severity": "MEDIUM",
                "label": "PASS",
                "evidence": "The completion summary matches the cited policy.",
                "policy_chunk_id": str(chunk_id) if chunk_id is not None else None,
                "recommendation": "Retain the completion evidence.",
                "confidence": 0.91,
            }
        ],
    }


class _Repository:
    def __init__(self, existing: QualityResultRecord | None = None) -> None:
        self.existing = existing
        self.saved: list[QualityResultRecord] = []

    async def find_result(self, tenant_id: UUID, job_id: UUID) -> QualityResultRecord | None:
        assert tenant_id == TENANT
        assert job_id == JOB
        return self.existing

    async def save_result(self, result: QualityResultRecord) -> QualityResultRecord:
        self.saved.append(result)
        self.existing = result
        return result


class _Index:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str, int]] = []

    async def search(self, tenant_id: UUID, query: str, limit: int = 5) -> RetrievalResult:
        self.calls.append((tenant_id, query, limit))
        return RetrievalResult(hits=(_hit(),), mode="hybrid")


class _Gateway:
    def __init__(
        self,
        payload: dict[str, object] | None = None,
        error: Exception | None = None,
        raw_content: str | None = None,
    ) -> None:
        self.payload = payload or _model_payload()
        self.error = error
        self.raw_content = raw_content
        self.requests: list[StructuredLLMRequest] = []

    async def generate_structured(self, request: StructuredLLMRequest) -> StructuredLLMResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        raw = self.raw_content or json.dumps(self.payload)
        return StructuredLLMResult(
            payload=self.payload,
            raw_content=raw,
            provider="synthetic-provider",
            model="synthetic-model",
            latency_ms=42,
            input_tokens=123,
            output_tokens=45,
            estimated_cost=0.0012,
        )


def _processor(
    *,
    repository: _Repository | None = None,
    gateway: _Gateway | None = None,
) -> tuple[QualityProcessor, _Repository, _Index, _Gateway]:
    repo = repository or _Repository()
    index = _Index()
    model = gateway or _Gateway()
    return (
        QualityProcessor(repository=repo, policy_index=index, gateway=model),
        repo,
        index,
        model,
    )


@pytest.mark.asyncio
async def test_model_success_saves_one_immutable_audited_result() -> None:
    processor, repository, index, gateway = _processor()

    result = await processor.process(_job())

    assert result.verdict == "PASS"
    assert result.confidence == 0.93
    assert len(result.findings) == 5
    assert result.findings[-1].source == "MODEL"
    assert result.model_call is not None
    assert result.model_call.provider == "synthetic-provider"
    assert result.model_call.prompt_version == "quality-inspection-v1"
    assert result.model_call.input_tokens == 123
    assert result.model_call.estimated_cost == 0.0012
    assert len(result.model_call.input_summary["request_hash"]) == 64
    assert len(result.model_call.response_summary["response_hash"]) == 64
    assert result.model_call.input_summary["retrieved_policy_chunks"] == [
        {
            "chunk_id": str(CHUNK),
            "document_key": "completion-policy",
            "document_version": 4,
        }
    ]
    assert repository.saved == [result]
    assert index.calls[0][0] == TENANT
    assert len(gateway.requests) == 1

    prompt = "\n".join(message.content for message in gateway.requests[0].messages)
    assert "contact_phone" not in prompt
    assert "must-not-enter-prompt" not in prompt
    assert "attachment_url" not in prompt
    assert "chain-of-thought" not in result.model_call.response_summary
    assert gateway.requests[0].request_id == result.model_call.request_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ProviderTimeoutError(),
        ProviderRateLimitError(),
        ProviderUnavailableError(),
        ProviderAuthError(),
    ],
)
async def test_provider_failures_preserve_standardized_retry_classification(
    error: Exception,
) -> None:
    processor, repository, _, _ = _processor(gateway=_Gateway(error=error))

    with pytest.raises(type(error)) as raised:
        await processor.process(_job())

    assert raised.value is error
    assert repository.saved == []


@pytest.mark.asyncio
async def test_malformed_json_from_gateway_is_not_persisted() -> None:
    processor, repository, _, _ = _processor(gateway=_Gateway(error=ProviderBadResponseError()))

    with pytest.raises(ProviderBadResponseError):
        await processor.process(_job())

    assert repository.saved == []


@pytest.mark.asyncio
async def test_schema_error_is_nonretryable_and_not_persisted() -> None:
    invalid = deepcopy(_model_payload())
    invalid["verdict"] = "MAYBE"
    processor, repository, _, _ = _processor(gateway=_Gateway(payload=invalid))

    with pytest.raises(QualityOutputError) as raised:
        await processor.process(_job())

    assert raised.value.retryable is False
    assert repository.saved == []


@pytest.mark.asyncio
async def test_ungrounded_finding_is_downgraded_to_uncertain() -> None:
    invented = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2")
    processor, _, _, _ = _processor(gateway=_Gateway(payload=_model_payload(chunk_id=invented)))

    result = await processor.process(_job())

    assert result.verdict == "UNCERTAIN"
    assert result.findings[-1].label == "UNCERTAIN"
    assert result.findings[-1].policy_chunk_id is None


@pytest.mark.asyncio
async def test_no_attachments_is_skip_and_never_calls_retrieval_or_model() -> None:
    processor, repository, index, gateway = _processor()

    result = await processor.process(_job(attachments=False))

    assert result.verdict == "SKIP"
    assert result.model_call is None
    assert repository.saved == [result]
    assert index.calls == []
    assert gateway.requests == []


@pytest.mark.asyncio
async def test_deterministic_failure_overrides_model_pass() -> None:
    processor, _, _, _ = _processor()

    result = await processor.process(_job(summary="   "))

    assert result.verdict == "FAIL"
    assert result.confidence == 1.0
    assert result.findings[0].rule_code == "REQUIRED_COMPLETION_SUMMARY"
    assert result.findings[0].label == "FAIL"


@pytest.mark.asyncio
async def test_duplicate_processing_returns_existing_before_any_external_call() -> None:
    first_processor, repository, _, _ = _processor()
    existing = await first_processor.process(_job())
    duplicate_processor, _, index, gateway = _processor(repository=repository)

    duplicate = await duplicate_processor.process(_job())

    assert duplicate is existing
    assert len(repository.saved) == 1
    assert index.calls == []
    assert gateway.requests == []


@pytest.mark.asyncio
async def test_audit_response_is_utf8_safely_truncated_to_eight_kibibytes() -> None:
    huge = json.dumps(_model_payload()) + ("质" * 9000)
    processor, _, _, _ = _processor(gateway=_Gateway(raw_content=huge))

    result = await processor.process(_job())

    assert result.model_call is not None
    raw = result.model_call.raw_response_truncated
    assert raw is not None
    assert len(raw.encode("utf-8")) <= 8192
    assert len(result.model_call.response_summary["response_hash"]) == 64
