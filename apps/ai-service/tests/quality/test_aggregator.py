from __future__ import annotations

from uuid import UUID

import pytest

from app.quality.aggregator import aggregate_verdict
from app.quality.rules import QualityFinding
from app.quality.schema import QualityModelFinding, QualityModelOutput


def _rule(label: str = "PASS") -> QualityFinding:
    return QualityFinding.model_validate(
        {
            "rule_code": "REQUIRED_COMPLETION_SUMMARY",
            "severity": "HIGH",
            "label": label,
            "evidence": {"present": label == "PASS"},
            "recommendation": "Keep deterministic evidence.",
            "confidence": 1.0,
            "source": "RULE",
        }
    )


def _model(verdict: str, finding_label: str | None = None) -> QualityModelOutput:
    findings = ()
    if finding_label is not None:
        findings = (
            QualityModelFinding.model_validate(
                {
                    "rule_code": "POLICY_COMPLETION_EVIDENCE",
                    "severity": "MEDIUM",
                    "label": finding_label,
                    "evidence": "Synthetic evidence.",
                    "policy_chunk_id": str(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1")),
                    "recommendation": "Keep synthetic evidence.",
                    "confidence": 0.8,
                }
            ),
        )
    return QualityModelOutput.model_validate(
        {"verdict": verdict, "confidence": 0.8, "findings": findings}
    )


def test_deterministic_failure_cannot_be_overridden_by_model_pass() -> None:
    assert aggregate_verdict((_rule("FAIL"),), _model("PASS")) == "FAIL"


@pytest.mark.parametrize(
    ("model_verdict", "finding_label", "expected"),
    [
        ("FAIL", None, "FAIL"),
        ("PASS", "FAIL", "FAIL"),
        ("UNCERTAIN", None, "UNCERTAIN"),
        ("PASS", "UNCERTAIN", "UNCERTAIN"),
        ("SKIP", None, "SKIP"),
        ("PASS", "SKIP", "SKIP"),
        ("PASS", None, "PASS"),
    ],
)
def test_aggregate_priority_is_fail_uncertain_skip_pass(
    model_verdict: str,
    finding_label: str | None,
    expected: str,
) -> None:
    assert aggregate_verdict((_rule(),), _model(model_verdict, finding_label)) == expected


def test_empty_model_findings_use_the_validated_model_verdict() -> None:
    assert aggregate_verdict((_rule(),), _model("PASS")) == "PASS"


def test_aggregator_rejects_non_rule_findings_in_deterministic_input() -> None:
    forged = _rule().model_copy(update={"source": "MODEL"})

    with pytest.raises(ValueError, match="deterministic finding"):
        aggregate_verdict((forged,), _model("PASS"))
