from __future__ import annotations

from datetime import datetime

import pytest

from app.quality.rules import (
    AttachmentSummary,
    QualityInput,
    RuleEngine,
)

RULE_CODES = (
    "REQUIRED_COMPLETION_SUMMARY",
    "COMPLETED_AT_RANGE",
    "SLA_COMPLETION",
    "REQUIRED_ATTACHMENT",
)


def _input(**overrides: object) -> QualityInput:
    values: dict[str, object] = {
        "completion_summary": "Replaced the synthetic valve and verified operation.",
        "created_at": "2026-07-20T08:00:00+08:00",
        "completed_at": "2026-07-20T09:00:00+08:00",
        "due_at": "2026-07-20T10:00:00+08:00",
        "attachments": (
            {
                "attachment_id": "synthetic-photo-1",
                "media_type": "image/jpeg",
                "size_bytes": 1024,
                "present": True,
            },
        ),
    }
    values.update(overrides)
    return QualityInput.model_validate(values)


def _labels(quality_input: QualityInput) -> dict[str, str]:
    return {finding.rule_code: finding.label for finding in RuleEngine().evaluate(quality_input)}


@pytest.mark.parametrize("summary", [None, "", "   "])
def test_required_completion_summary_fails_only_when_missing_or_blank(
    summary: str | None,
) -> None:
    findings = RuleEngine().evaluate(_input(completion_summary=summary))

    assert findings[0].rule_code == "REQUIRED_COMPLETION_SUMMARY"
    assert findings[0].label == "FAIL"
    assert findings[0].severity == "HIGH"
    assert findings[0].evidence == {"present": False, "length": 0}
    assert _labels(_input(completion_summary="done"))["REQUIRED_COMPLETION_SUMMARY"] == "PASS"


def test_completed_at_range_handles_offset_aware_boundaries() -> None:
    equal = _labels(
        _input(
            created_at="2026-07-20T08:00:00+08:00",
            completed_at="2026-07-20T00:00:00Z",
        )
    )
    before = _labels(
        _input(
            created_at="2026-07-20T08:00:00+08:00",
            completed_at="2026-07-19T23:59:59Z",
        )
    )

    assert equal["COMPLETED_AT_RANGE"] == "PASS"
    assert before["COMPLETED_AT_RANGE"] == "FAIL"


@pytest.mark.parametrize("field", ["created_at", "completed_at", "due_at"])
def test_quality_input_rejects_naive_timestamps(field: str) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _input(**{field: datetime.fromisoformat("2026-07-20T08:00:00")})


def test_sla_due_time_equality_passes_and_one_microsecond_late_fails() -> None:
    equal = _labels(
        _input(
            completed_at="2026-07-20T10:00:00+08:00",
            due_at="2026-07-20T10:00:00+08:00",
        )
    )
    late = RuleEngine().evaluate(
        _input(
            completed_at="2026-07-20T10:00:00.000001+08:00",
            due_at="2026-07-20T10:00:00+08:00",
        )
    )

    assert equal["SLA_COMPLETION"] == "PASS"
    sla = next(finding for finding in late if finding.rule_code == "SLA_COMPLETION")
    assert sla.label == "FAIL"
    assert sla.evidence["overdue_seconds"] == pytest.approx(0.000001)


@pytest.mark.parametrize("attachments", [None, (), []])
def test_required_attachment_fails_for_missing_or_empty_list(attachments: object) -> None:
    finding = RuleEngine().evaluate(_input(attachments=attachments))[-1]

    assert finding.rule_code == "REQUIRED_ATTACHMENT"
    assert finding.label == "FAIL"
    assert finding.evidence == {"attachment_count": 0, "present_count": 0}


def test_required_attachment_needs_at_least_one_present_item() -> None:
    absent = _labels(
        _input(
            attachments=(
                AttachmentSummary(
                    attachment_id="missing-synthetic-photo",
                    media_type="image/jpeg",
                    size_bytes=0,
                    present=False,
                ),
            )
        )
    )

    assert absent["REQUIRED_ATTACHMENT"] == "FAIL"
    assert _labels(_input())["REQUIRED_ATTACHMENT"] == "PASS"


def test_multiple_failures_remain_stably_ordered_and_auditable() -> None:
    findings = RuleEngine().evaluate(
        _input(
            completion_summary=" ",
            created_at="2026-07-20T09:00:00+08:00",
            completed_at="2026-07-20T08:00:00+08:00",
            due_at="2026-07-20T07:00:00+08:00",
            attachments=(),
        )
    )

    assert tuple(finding.rule_code for finding in findings) == RULE_CODES
    assert tuple(finding.label for finding in findings) == ("FAIL", "FAIL", "FAIL", "FAIL")
    assert all(finding.source == "RULE" for finding in findings)
    assert all(finding.confidence == 1.0 for finding in findings)
    assert all(finding.evidence for finding in findings)
    assert all(finding.recommendation.strip() for finding in findings)
