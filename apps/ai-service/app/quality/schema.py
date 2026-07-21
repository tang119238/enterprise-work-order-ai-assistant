from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

QualityVerdict = Literal["PASS", "FAIL", "UNCERTAIN", "SKIP"]
ModelSeverity = Literal["LOW", "MEDIUM", "HIGH"]
ModelLabel = Literal["PASS", "FAIL", "UNCERTAIN", "SKIP"]

_RULE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")


class QualityModelFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_code: str = Field(min_length=3, max_length=128)
    severity: ModelSeverity
    label: ModelLabel
    evidence: str = Field(min_length=1, max_length=4000)
    policy_chunk_id: UUID | None
    recommendation: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)

    @field_validator("rule_code")
    @classmethod
    def validate_rule_code_shape(cls, value: str) -> str:
        if _RULE_CODE.fullmatch(value) is None:
            raise ValueError("invalid policy rule code")
        return value

    @field_validator("evidence", "recommendation")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("model finding text must not be blank")
        return stripped


class QualityModelOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: QualityVerdict
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    findings: tuple[QualityModelFinding, ...] = Field(max_length=20)

    @model_validator(mode="after")
    def reject_duplicate_rules(self) -> QualityModelOutput:
        rule_codes = [finding.rule_code for finding in self.findings]
        if len(rule_codes) != len(set(rule_codes)):
            raise ValueError("model output contains duplicate policy rules")
        return self


def validate_model_output(
    payload: Mapping[str, Any] | object,
    *,
    allowed_rule_codes: Iterable[str],
) -> QualityModelOutput:
    allowed = frozenset(allowed_rule_codes)
    if any(not isinstance(code, str) or _RULE_CODE.fullmatch(code) is None for code in allowed):
        raise ValueError("policy catalog contains an invalid rule code")
    output = QualityModelOutput.model_validate(payload)
    unknown = sorted({finding.rule_code for finding in output.findings} - allowed)
    if unknown:
        raise ValueError(f"unknown policy rule: {unknown[0]}")
    return output


def validate_policy_evidence(
    output: QualityModelOutput,
    retrieved_chunk_ids: Iterable[UUID | str],
) -> QualityModelOutput:
    if not isinstance(output, QualityModelOutput):
        raise TypeError("output must be a QualityModelOutput")
    retrieved = frozenset(_uuid(chunk_id) for chunk_id in retrieved_chunk_ids)
    changed = False
    normalized: list[QualityModelFinding] = []
    for finding in output.findings:
        if finding.policy_chunk_id is None or finding.policy_chunk_id not in retrieved:
            changed = True
            normalized.append(
                finding.model_copy(update={"label": "UNCERTAIN", "policy_chunk_id": None})
            )
        else:
            normalized.append(finding)
    if not changed:
        return output

    verdict: QualityVerdict
    if any(finding.label == "FAIL" for finding in normalized):
        verdict = "FAIL"
    elif output.verdict == "SKIP":
        verdict = "SKIP"
    else:
        verdict = "UNCERTAIN"
    return output.model_copy(update={"verdict": verdict, "findings": tuple(normalized)})


def _uuid(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(value)
    except (TypeError, ValueError) as error:
        raise ValueError("retrieved chunk IDs must be UUIDs") from error
