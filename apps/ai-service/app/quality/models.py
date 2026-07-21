from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

QualityJobStatus = Literal[
    "PENDING",
    "RUNNING",
    "RETRY_WAIT",
    "SUCCEEDED",
    "FAILED",
    "SKIPPED",
]

_FORBIDDEN_EVENT_KEYS = {
    "attachment_url",
    "attachment_uri",
    "database_url",
    "password",
    "secret",
    "token",
    "credential",
}


class ClaimedQualityEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    tenant_id: UUID
    work_order_id: UUID
    work_order_version: int = Field(ge=0)
    work_order_snapshot: dict[str, Any]
    attachments_summary: tuple[dict[str, Any], ...] = ()
    inspection_round: int = Field(ge=1)
    attempt: int = Field(ge=1)
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_snapshot_boundary(self) -> ClaimedQualityEvent:
        snapshot = self.work_order_snapshot
        if snapshot.get("id") != str(self.work_order_id):
            raise ValueError("snapshot work-order identity does not match event")
        if snapshot.get("tenant_id") != str(self.tenant_id):
            raise ValueError("snapshot tenant does not match event")
        if snapshot.get("version") != self.work_order_version:
            raise ValueError("snapshot version does not match event")
        if snapshot.get("status") != "COMPLETED":
            raise ValueError("quality events require a completed snapshot")
        if _contains_forbidden_key(snapshot) or _contains_forbidden_key(self.attachments_summary):
            raise ValueError("event contains a forbidden sensitive field")
        return self


class QualityJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    tenant_id: UUID
    work_order_id: UUID
    work_order_version: int = Field(ge=0)
    inspection_round: int = Field(ge=1)
    business_key: str
    status: QualityJobStatus


class ClaimedQualityJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    tenant_id: UUID
    work_order_id: UUID
    work_order_version: int = Field(ge=0)
    inspection_round: int = Field(ge=1)
    retry_count: int = Field(ge=0)
    work_order_snapshot: dict[str, Any]
    attachments_summary: tuple[dict[str, Any], ...] = ()

    @model_validator(mode="after")
    def validate_claim_boundary(self) -> ClaimedQualityJob:
        snapshot = self.work_order_snapshot
        if snapshot.get("id") != str(self.work_order_id):
            raise ValueError("snapshot work-order identity does not match job")
        if snapshot.get("tenant_id") != str(self.tenant_id):
            raise ValueError("snapshot tenant does not match job")
        if snapshot.get("version") != self.work_order_version:
            raise ValueError("snapshot version does not match job")
        if snapshot.get("status") != "COMPLETED":
            raise ValueError("quality jobs require a completed snapshot")
        if _contains_forbidden_key(snapshot) or _contains_forbidden_key(self.attachments_summary):
            raise ValueError("job contains a forbidden sensitive field")
        return self


class QualityFindingRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ordinal: int = Field(ge=0)
    rule_code: str = Field(min_length=1, max_length=128)
    severity: Literal["LOW", "MEDIUM", "HIGH"]
    label: Literal["PASS", "FAIL", "UNCERTAIN", "SKIP"]
    evidence: dict[str, Any]
    policy_chunk_id: UUID | None = None
    recommendation: str
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    source: Literal["RULE", "MODEL"]


class ModelCallAuditRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    provider: str = Field(min_length=1, max_length=100)
    model_name: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=100)
    request_id: str = Field(min_length=1, max_length=200)
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost: Decimal | float | None = Field(default=None, ge=0)
    input_summary: dict[str, Any]
    response_summary: dict[str, Any]
    raw_response_truncated: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class QualityResultRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    tenant_id: UUID
    quality_job_id: UUID
    work_order_id: UUID
    work_order_version: int = Field(ge=0)
    inspection_round: int = Field(ge=1)
    verdict: Literal["PASS", "FAIL", "UNCERTAIN", "SKIP"]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    work_order_snapshot: dict[str, Any]
    policy_versions: dict[str, int]
    attachment_summary: tuple[dict[str, Any], ...]
    findings: tuple[QualityFindingRecord, ...]
    model_call: ModelCallAuditRecord | None = None


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_EVENT_KEYS or normalized.endswith(("_url", "_uri")):
                return True
            if _contains_forbidden_key(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_forbidden_key(item) for item in value)
    return False
