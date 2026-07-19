from __future__ import annotations

from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.quality.schema import (
    QualityModelOutput,
    validate_model_output,
    validate_policy_evidence,
)

ALLOWED_RULE = "POLICY_COMPLETION_EVIDENCE"
RETRIEVED_CHUNK = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1")


def _payload() -> dict[str, object]:
    return {
        "verdict": "PASS",
        "confidence": 0.92,
        "findings": [
            {
                "rule_code": ALLOWED_RULE,
                "severity": "MEDIUM",
                "label": "PASS",
                "evidence": "The completion note matches the cited synthetic policy.",
                "policy_chunk_id": str(RETRIEVED_CHUNK),
                "recommendation": "Keep the cited evidence with the work order.",
                "confidence": 0.91,
            }
        ],
    }


def test_strict_model_output_accepts_only_catalogued_schema() -> None:
    output = validate_model_output(_payload(), allowed_rule_codes={ALLOWED_RULE})

    assert isinstance(output, QualityModelOutput)
    assert output.verdict == "PASS"
    assert output.findings[0].policy_chunk_id == RETRIEVED_CHUNK
    assert output.model_dump(mode="json") == _payload()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("unexpected",), "forged"),
        (("findings", 0, "reasoning"), "hidden reasoning"),
        (("verdict",), "MAYBE"),
        (("findings", 0, "severity"), "CRITICAL"),
        (("findings", 0, "label"), "MAYBE"),
        (("confidence",), -0.01),
        (("confidence",), 1.01),
        (("findings", 0, "confidence"), -0.01),
        (("findings", 0, "confidence"), 1.01),
        (("findings", 0, "policy_chunk_id"), "not-a-uuid"),
    ],
)
def test_model_output_rejects_unknown_fields_enums_bounds_and_chunk_ids(
    path: tuple[object, ...],
    value: object,
) -> None:
    payload = deepcopy(_payload())
    target: object = payload
    for segment in path[:-1]:
        target = target[segment]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValidationError):
        validate_model_output(payload, allowed_rule_codes={ALLOWED_RULE})


def test_model_output_rejects_unknown_policy_rule_from_model() -> None:
    payload = _payload()
    payload["findings"][0]["rule_code"] = "MODEL_INVENTED_RULE"  # type: ignore[index]

    with pytest.raises(ValueError, match="unknown policy rule"):
        validate_model_output(payload, allowed_rule_codes={ALLOWED_RULE})


def test_model_output_rejects_more_than_twenty_findings() -> None:
    payload = _payload()
    payload["findings"] = [deepcopy(payload["findings"][0]) for _ in range(21)]  # type: ignore[index]

    with pytest.raises(ValidationError):
        validate_model_output(payload, allowed_rule_codes={ALLOWED_RULE})


def test_policy_evidence_downgrades_missing_or_invented_chunk_references() -> None:
    invented = validate_model_output(_payload(), allowed_rule_codes={ALLOWED_RULE})
    missing_payload = _payload()
    missing_payload["findings"][0]["policy_chunk_id"] = None  # type: ignore[index]
    missing = validate_model_output(missing_payload, allowed_rule_codes={ALLOWED_RULE})

    normalized_invented = validate_policy_evidence(invented, retrieved_chunk_ids=set())
    normalized_missing = validate_policy_evidence(
        missing,
        retrieved_chunk_ids={RETRIEVED_CHUNK},
    )

    assert normalized_invented.findings[0].label == "UNCERTAIN"
    assert normalized_invented.findings[0].policy_chunk_id is None
    assert normalized_missing.findings[0].label == "UNCERTAIN"
    assert normalized_missing.findings[0].policy_chunk_id is None
    assert invented.findings[0].label == "PASS"
    assert invented.findings[0].policy_chunk_id == RETRIEVED_CHUNK


def test_policy_evidence_keeps_grounded_finding_unchanged() -> None:
    output = validate_model_output(_payload(), allowed_rule_codes={ALLOWED_RULE})

    assert validate_policy_evidence(output, {RETRIEVED_CHUNK}) is output


def test_empty_findings_are_valid_and_frozen() -> None:
    output = validate_model_output(
        {"verdict": "PASS", "confidence": 0.8, "findings": []},
        allowed_rule_codes={ALLOWED_RULE},
    )

    assert output.findings == ()
    with pytest.raises(ValidationError):
        output.verdict = "FAIL"  # type: ignore[misc]
