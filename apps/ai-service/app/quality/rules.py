from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RuleCode = Literal[
    "REQUIRED_COMPLETION_SUMMARY",
    "COMPLETED_AT_RANGE",
    "SLA_COMPLETION",
    "REQUIRED_ATTACHMENT",
]
FindingSeverity = Literal["LOW", "MEDIUM", "HIGH"]
FindingLabel = Literal["PASS", "FAIL", "UNCERTAIN", "SKIP"]
FindingSource = Literal["RULE", "MODEL"]
type EvidenceValue = str | int | float | bool | None


class AttachmentSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    attachment_id: str = Field(min_length=1, max_length=200)
    media_type: str = Field(min_length=1, max_length=200)
    size_bytes: int = Field(ge=0)
    present: bool = True


class QualityInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    completion_summary: str | None
    created_at: datetime | None
    completed_at: datetime | None
    due_at: datetime | None
    attachments: tuple[AttachmentSummary, ...] | None = None

    @field_validator("created_at", "completed_at", "due_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("quality timestamps must be timezone-aware")
        return value


class QualityFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_code: RuleCode
    severity: FindingSeverity
    label: FindingLabel
    evidence: dict[str, EvidenceValue]
    policy_chunk_id: str | None = None
    recommendation: str
    confidence: float = Field(ge=0, le=1)
    source: FindingSource


class RuleEngine:
    def evaluate(self, quality_input: QualityInput) -> tuple[QualityFinding, ...]:
        if not isinstance(quality_input, QualityInput):
            raise TypeError("quality_input must be a QualityInput")
        return (
            self._required_completion_summary(quality_input),
            self._completed_at_range(quality_input),
            self._sla_completion(quality_input),
            self._required_attachment(quality_input),
        )

    @staticmethod
    def _required_completion_summary(quality_input: QualityInput) -> QualityFinding:
        summary = (quality_input.completion_summary or "").strip()
        passed = bool(summary)
        return _finding(
            rule_code="REQUIRED_COMPLETION_SUMMARY",
            severity="HIGH",
            passed=passed,
            evidence={"present": passed, "length": len(summary)},
            recommendation=(
                "Completion summary is present."
                if passed
                else "Add a verifiable completion summary before review."
            ),
        )

    @staticmethod
    def _completed_at_range(quality_input: QualityInput) -> QualityFinding:
        created_at = quality_input.created_at
        completed_at = quality_input.completed_at
        passed = created_at is not None and completed_at is not None and completed_at >= created_at
        evidence: dict[str, EvidenceValue] = {
            "created_at": created_at.isoformat() if created_at is not None else None,
            "completed_at": completed_at.isoformat() if completed_at is not None else None,
            "completed_not_before_created": passed,
        }
        return _finding(
            rule_code="COMPLETED_AT_RANGE",
            severity="HIGH",
            passed=passed,
            evidence=evidence,
            recommendation=(
                "Completion timestamp is within the work-order lifetime."
                if passed
                else "Correct the completion timestamp so it is not before creation."
            ),
        )

    @staticmethod
    def _sla_completion(quality_input: QualityInput) -> QualityFinding:
        completed_at = quality_input.completed_at
        due_at = quality_input.due_at
        if completed_at is None or due_at is None:
            overdue_seconds = None
            passed = False
        else:
            overdue_seconds = max(0.0, (completed_at - due_at).total_seconds())
            passed = completed_at <= due_at
        return _finding(
            rule_code="SLA_COMPLETION",
            severity="MEDIUM",
            passed=passed,
            evidence={
                "completed_at": completed_at.isoformat() if completed_at is not None else None,
                "due_at": due_at.isoformat() if due_at is not None else None,
                "overdue_seconds": overdue_seconds,
            },
            recommendation=(
                "Completion met the due-time boundary."
                if passed
                else "Record and review the reason for completion after the due time."
            ),
        )

    @staticmethod
    def _required_attachment(quality_input: QualityInput) -> QualityFinding:
        attachments = quality_input.attachments or ()
        present_count = sum(1 for attachment in attachments if attachment.present)
        passed = present_count > 0
        return _finding(
            rule_code="REQUIRED_ATTACHMENT",
            severity="HIGH",
            passed=passed,
            evidence={
                "attachment_count": len(attachments),
                "present_count": present_count,
            },
            recommendation=(
                "At least one completion attachment is present."
                if passed
                else "Attach at least one completion artifact before review."
            ),
        )


def _finding(
    *,
    rule_code: RuleCode,
    severity: FindingSeverity,
    passed: bool,
    evidence: dict[str, EvidenceValue],
    recommendation: str,
) -> QualityFinding:
    return QualityFinding(
        rule_code=rule_code,
        severity=severity,
        label="PASS" if passed else "FAIL",
        evidence=evidence,
        recommendation=recommendation,
        confidence=1.0,
        source="RULE",
    )
