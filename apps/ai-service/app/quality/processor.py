from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid5

from pydantic import ValidationError

from app.knowledge.models import RetrievalHit, RetrievalResult
from app.llm.contracts import LLMMessage, StructuredLLMRequest, StructuredLLMResult
from app.quality.aggregator import aggregate_verdict
from app.quality.models import (
    ClaimedQualityJob,
    ModelCallAuditRecord,
    QualityFindingRecord,
    QualityResultRecord,
)
from app.quality.rules import AttachmentSummary, QualityFinding, QualityInput, RuleEngine
from app.quality.schema import (
    QualityModelOutput,
    QualityVerdict,
    validate_model_output,
    validate_policy_evidence,
)

PROMPT_VERSION = "quality-inspection-v1"
_RESULT_NAMESPACE = UUID("05533578-0377-52bb-a97e-c4c6f2460001")
_AUDIT_NAMESPACE = UUID("05533578-0377-52bb-a97e-c4c6f2460002")
_REQUEST_NAMESPACE = UUID("05533578-0377-52bb-a97e-c4c6f2460003")
_POLICY_CHUNK_NAMESPACE = UUID("05533578-0377-52bb-a97e-c4c6f2460004")
_MAX_RAW_RESPONSE_BYTES = 8 * 1024
_APPROVED_SNAPSHOT_FIELDS = (
    "id",
    "tenant_id",
    "version",
    "status",
    "title",
    "description",
    "order_type",
    "priority",
    "space_path",
    "completion_summary",
    "created_at",
    "due_at",
    "completed_at",
)


class QualityOutputError(ValueError):
    code = "QUALITY_OUTPUT_INVALID"
    retryable = False


class QualityResultRepository(Protocol):
    async def find_result(
        self,
        tenant_id: UUID,
        job_id: UUID,
    ) -> QualityResultRecord | None: ...

    async def save_result(self, result: QualityResultRecord) -> QualityResultRecord: ...


class TenantPolicyIndex(Protocol):
    async def search(
        self,
        tenant_id: UUID,
        query: str,
        limit: int = 5,
    ) -> RetrievalResult: ...


class StructuredGateway(Protocol):
    async def generate_structured(
        self,
        request: StructuredLLMRequest,
    ) -> StructuredLLMResult: ...


class QualityProcessor:
    def __init__(
        self,
        *,
        repository: QualityResultRepository,
        policy_index: TenantPolicyIndex,
        gateway: StructuredGateway,
        rule_engine: RuleEngine | None = None,
        prompt_version: str = PROMPT_VERSION,
        policy_limit: int = 5,
    ) -> None:
        if not prompt_version.strip():
            raise ValueError("prompt_version must be nonblank")
        if isinstance(policy_limit, bool) or not 1 <= policy_limit <= 20:
            raise ValueError("policy_limit must be between 1 and 20")
        self._repository = repository
        self._policy_index = policy_index
        self._gateway = gateway
        self._rule_engine = rule_engine or RuleEngine()
        self._prompt_version = prompt_version
        self._policy_limit = policy_limit

    async def process(self, job: ClaimedQualityJob) -> QualityResultRecord:
        if not isinstance(job, ClaimedQualityJob):
            raise TypeError("job must be a ClaimedQualityJob")
        existing = await self._repository.find_result(job.tenant_id, job.id)
        if existing is not None:
            return existing

        safe_snapshot = _safe_snapshot(job.work_order_snapshot)
        attachments = _safe_attachments(job.attachments_summary)
        quality_input = QualityInput(
            completion_summary=_optional_text(safe_snapshot.get("completion_summary")),
            created_at=_timestamp(safe_snapshot.get("created_at")),
            completed_at=_timestamp(safe_snapshot.get("completed_at")),
            due_at=_timestamp(safe_snapshot.get("due_at")),
            attachments=attachments,
        )
        rule_findings = self._rule_engine.evaluate(quality_input)

        if not any(attachment.present for attachment in attachments):
            result = _result_record(
                job=job,
                verdict="SKIP",
                confidence=1.0,
                safe_snapshot=safe_snapshot,
                attachments=attachments,
                policy_versions={},
                rule_findings=rule_findings,
                model_output=None,
                model_call=None,
            )
            return await self._repository.save_result(result)

        retrieval = await self._policy_index.search(
            job.tenant_id,
            _retrieval_query(safe_snapshot),
            limit=self._policy_limit,
        )
        request = _structured_request(
            job=job,
            safe_snapshot=safe_snapshot,
            attachments=attachments,
            hits=retrieval.hits,
            prompt_version=self._prompt_version,
        )
        response = await self._gateway.generate_structured(request)
        model_output = _validated_output(response.payload, retrieval.hits)
        grounded_output = validate_policy_evidence(
            model_output,
            retrieved_chunk_ids=(policy_chunk_uuid(hit.chunk_id) for hit in retrieval.hits),
        )
        verdict = aggregate_verdict(rule_findings, grounded_output)
        confidence = (
            1.0
            if any(finding.label == "FAIL" for finding in rule_findings)
            else grounded_output.confidence
        )
        audit = _audit_record(job, request, response, grounded_output, retrieval.hits)
        result = _result_record(
            job=job,
            verdict=verdict,
            confidence=confidence,
            safe_snapshot=safe_snapshot,
            attachments=attachments,
            policy_versions=_policy_versions(retrieval.hits),
            rule_findings=rule_findings,
            model_output=grounded_output,
            model_call=audit,
        )
        return await self._repository.save_result(result)


def policy_rule_code(chunk_id: UUID | str) -> str:
    parsed = policy_chunk_uuid(chunk_id)
    return f"POLICY_{parsed.hex.upper()}"


def policy_chunk_uuid(chunk_id: UUID | str) -> UUID:
    if isinstance(chunk_id, UUID):
        return chunk_id
    try:
        return UUID(chunk_id)
    except ValueError:
        if not chunk_id.strip():
            raise ValueError("policy chunk ID must be nonblank") from None
        return uuid5(_POLICY_CHUNK_NAMESPACE, chunk_id)


def _safe_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {key: snapshot[key] for key in _APPROVED_SNAPSHOT_FIELDS if key in snapshot}


def _safe_attachments(
    attachments: Sequence[Mapping[str, Any]],
) -> tuple[AttachmentSummary, ...]:
    return tuple(
        AttachmentSummary.model_validate(
            {
                "attachment_id": attachment.get("attachment_id"),
                "media_type": attachment.get("media_type"),
                "size_bytes": attachment.get("size_bytes"),
                "present": attachment.get("present", True),
            }
        )
        for attachment in attachments
    )


def _timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    else:
        raise ValueError("quality timestamp must be an ISO-8601 string")
    # The Java work-order store currently uses UTC LocalDateTime values. The
    # service boundary makes that legacy convention explicit before evaluation.
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("completion_summary must be text")
    return value


def _retrieval_query(snapshot: Mapping[str, Any]) -> str:
    values = [
        snapshot.get("title"),
        snapshot.get("description"),
        snapshot.get("order_type"),
        snapshot.get("space_path"),
        snapshot.get("completion_summary"),
    ]
    return "\n".join(value.strip() for value in values if isinstance(value, str) and value.strip())


def _structured_request(
    *,
    job: ClaimedQualityJob,
    safe_snapshot: Mapping[str, Any],
    attachments: Sequence[AttachmentSummary],
    hits: Sequence[RetrievalHit],
    prompt_version: str,
) -> StructuredLLMRequest:
    policy_chunks = [
        {
            "rule_code": policy_rule_code(hit.chunk_id),
            "policy_chunk_id": str(policy_chunk_uuid(hit.chunk_id)),
            "document_key": hit.document_key,
            "document_version": hit.document_version,
            "title": hit.title,
            "section": hit.section,
            "text": hit.text,
        }
        for hit in hits
    ]
    prompt_payload = {
        "work_order": dict(safe_snapshot),
        "attachments": [attachment.model_dump(mode="json") for attachment in attachments],
        "policy_chunks": policy_chunks,
    }
    return StructuredLLMRequest(
        messages=(
            LLMMessage(
                role="system",
                content=(
                    "Inspect the completed work order only against the supplied policy chunks. "
                    "Return only the requested JSON object. Cite a supplied policy_chunk_id for "
                    "every policy finding; otherwise label it UNCERTAIN."
                ),
            ),
            LLMMessage(
                role="user",
                content=json.dumps(
                    prompt_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
        ),
        response_schema=QualityModelOutput.model_json_schema(),
        prompt_version=prompt_version,
        request_id=str(uuid5(_REQUEST_NAMESPACE, f"{job.tenant_id}:{job.id}:{prompt_version}")),
    )


def _validated_output(
    payload: Mapping[str, Any],
    hits: Sequence[RetrievalHit],
) -> QualityModelOutput:
    try:
        return validate_model_output(
            payload,
            allowed_rule_codes=(policy_rule_code(hit.chunk_id) for hit in hits),
        )
    except (ValidationError, TypeError, ValueError) as error:
        raise QualityOutputError("model output failed the quality schema") from error


def _audit_record(
    job: ClaimedQualityJob,
    request: StructuredLLMRequest,
    response: StructuredLLMResult,
    output: QualityModelOutput,
    hits: Sequence[RetrievalHit],
) -> ModelCallAuditRecord:
    request_material = json.dumps(
        {
            "messages": [
                {"role": message.role, "content": message.content} for message in request.messages
            ],
            "prompt_version": request.prompt_version,
            "response_schema": request.response_schema,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return ModelCallAuditRecord(
        id=uuid5(_AUDIT_NAMESPACE, f"{job.tenant_id}:{job.id}:{request.request_id}"),
        provider=response.provider,
        model_name=response.model,
        prompt_version=request.prompt_version,
        request_id=request.request_id,
        latency_ms=response.latency_ms,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        estimated_cost=response.estimated_cost,
        input_summary={
            "request_hash": _sha256(request_material),
            "snapshot_fields": sorted(_safe_snapshot(job.work_order_snapshot)),
            "attachment_count": len(job.attachments_summary),
            "retrieved_policy_chunks": [
                {
                    "chunk_id": hit.chunk_id,
                    "document_key": hit.document_key,
                    "document_version": hit.document_version,
                }
                for hit in hits
            ],
        },
        response_summary={
            "response_hash": _sha256(response.raw_content),
            "verdict": output.verdict,
            "finding_count": len(output.findings),
        },
        raw_response_truncated=_truncate_utf8(response.raw_content, _MAX_RAW_RESPONSE_BYTES),
    )


def _result_record(
    *,
    job: ClaimedQualityJob,
    verdict: QualityVerdict,
    confidence: float,
    safe_snapshot: dict[str, Any],
    attachments: Sequence[AttachmentSummary],
    policy_versions: dict[str, int],
    rule_findings: Sequence[QualityFinding],
    model_output: QualityModelOutput | None,
    model_call: ModelCallAuditRecord | None,
) -> QualityResultRecord:
    findings: list[QualityFindingRecord] = []
    for ordinal, rule_finding in enumerate(rule_findings):
        findings.append(
            QualityFindingRecord(
                ordinal=ordinal,
                rule_code=rule_finding.rule_code,
                severity=rule_finding.severity,
                label=rule_finding.label,
                evidence=rule_finding.evidence,
                policy_chunk_id=None,
                recommendation=rule_finding.recommendation,
                confidence=rule_finding.confidence,
                source="RULE",
            )
        )
    if model_output is not None:
        for model_finding in model_output.findings:
            findings.append(
                QualityFindingRecord(
                    ordinal=len(findings),
                    rule_code=model_finding.rule_code,
                    severity=model_finding.severity,
                    label=model_finding.label,
                    evidence={"summary": model_finding.evidence},
                    policy_chunk_id=model_finding.policy_chunk_id,
                    recommendation=model_finding.recommendation,
                    confidence=model_finding.confidence,
                    source="MODEL",
                )
            )
    return QualityResultRecord(
        id=uuid5(_RESULT_NAMESPACE, f"{job.tenant_id}:{job.id}"),
        tenant_id=job.tenant_id,
        quality_job_id=job.id,
        work_order_id=job.work_order_id,
        work_order_version=job.work_order_version,
        inspection_round=job.inspection_round,
        verdict=verdict,
        confidence=confidence,
        work_order_snapshot=safe_snapshot,
        policy_versions=policy_versions,
        attachment_summary=tuple(attachment.model_dump(mode="json") for attachment in attachments),
        findings=tuple(findings),
        model_call=model_call,
    )


def _policy_versions(hits: Sequence[RetrievalHit]) -> dict[str, int]:
    versions: dict[str, int] = {}
    for hit in hits:
        versions[hit.document_key] = max(versions.get(hit.document_key, 0), hit.document_version)
    return versions


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")
