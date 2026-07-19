from __future__ import annotations

from datetime import datetime
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
