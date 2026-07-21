from __future__ import annotations

from collections.abc import Sequence

from app.quality.rules import QualityFinding
from app.quality.schema import QualityModelOutput, QualityVerdict


def aggregate_verdict(
    rule_findings: Sequence[QualityFinding],
    model_output: QualityModelOutput,
) -> QualityVerdict:
    if not isinstance(model_output, QualityModelOutput):
        raise TypeError("model_output must be a QualityModelOutput")
    if any(finding.source != "RULE" for finding in rule_findings):
        raise ValueError("deterministic finding input must have RULE source")

    rule_labels = {finding.label for finding in rule_findings}
    model_labels = {finding.label for finding in model_output.findings}
    if "FAIL" in rule_labels:
        return "FAIL"
    if model_output.verdict == "FAIL" or "FAIL" in model_labels:
        return "FAIL"
    if (
        "UNCERTAIN" in rule_labels
        or model_output.verdict == "UNCERTAIN"
        or "UNCERTAIN" in model_labels
    ):
        return "UNCERTAIN"
    if "SKIP" in rule_labels or model_output.verdict == "SKIP" or "SKIP" in model_labels:
        return "SKIP"
    return "PASS"
