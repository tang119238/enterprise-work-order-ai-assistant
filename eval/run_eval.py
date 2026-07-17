from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DOCUMENT_ID_PATTERN = re.compile(
    r"<!--\s*document_id:\s*([a-z0-9-]+)\s*-->", re.IGNORECASE
)
REQUIRED_CATEGORY_COUNTS = {"knowledge": 10, "work_order": 10, "combined": 10}


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    category: str
    message: str
    expected_document_ids: frozenset[str]
    expected_tools: frozenset[str]
    required_facts: frozenset[str]
    forbidden_facts: frozenset[str]


@dataclass(frozen=True)
class CaseResult:
    retrieval_hit: bool
    retrieval_hits: int
    retrieval_expected: int
    citations_valid: bool
    valid_citations: int
    total_citations: int
    tools_correct: bool
    required_facts_present: bool
    forbidden_facts_absent: bool


def evaluate_case(
    *,
    expected_document_ids: set[str] | frozenset[str],
    expected_tools: set[str] | frozenset[str],
    required_facts: set[str] | frozenset[str],
    forbidden_facts: set[str] | frozenset[str],
    response: Mapping[str, object],
    policy_texts: Mapping[str, str],
) -> CaseResult:
    citations = _mapping_items(response.get("citations"))
    actual_document_ids = {
        document_id
        for citation in citations
        if (document_id := _non_blank_string(citation.get("document_id"))) is not None
    }
    retrieval_hits = len(expected_document_ids & actual_document_ids)
    retrieval_expected = len(expected_document_ids)

    valid_citations = 0
    for citation in citations:
        if _citation_is_valid(citation, policy_texts):
            valid_citations += 1

    tool_calls = _mapping_items(response.get("tool_calls"))
    actual_tools = {
        tool_name
        for call in tool_calls
        if call.get("status") == "success"
        and (tool_name := _non_blank_string(call.get("name"))) is not None
    }
    answer = response.get("answer") if isinstance(response.get("answer"), str) else ""

    return CaseResult(
        retrieval_hit=(
            retrieval_hits == retrieval_expected
            if retrieval_expected
            else not citations
        ),
        retrieval_hits=retrieval_hits,
        retrieval_expected=retrieval_expected,
        citations_valid=valid_citations == len(citations),
        valid_citations=valid_citations,
        total_citations=len(citations),
        tools_correct=actual_tools == expected_tools,
        required_facts_present=all(fact in answer for fact in required_facts),
        forbidden_facts_absent=all(fact not in answer for fact in forbidden_facts),
    )


def load_cases(path: Path) -> list[EvaluationCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Evaluation questions must be a JSON list")

    cases: list[EvaluationCase] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Every evaluation case must be a JSON object")
        cases.append(
            EvaluationCase(
                case_id=_required_string(item, "id"),
                category=_required_string(item, "category"),
                message=_required_string(item, "message"),
                expected_document_ids=frozenset(
                    _string_list(item, "expected_document_ids")
                ),
                expected_tools=frozenset(_string_list(item, "expected_tools")),
                required_facts=frozenset(_string_list(item, "required_facts")),
                forbidden_facts=frozenset(_string_list(item, "forbidden_facts")),
            )
        )

    category_counts = Counter(case.category for case in cases)
    if len(cases) != 30 or dict(category_counts) != REQUIRED_CATEGORY_COUNTS:
        raise ValueError(
            "Evaluation set must contain exactly 10 knowledge, 10 work_order, "
            "and 10 combined cases"
        )
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("Evaluation case ids must be unique")
    return cases


def load_policy_texts(directory: Path) -> dict[str, str]:
    policy_texts: dict[str, str] = {}
    for path in sorted(directory.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        match = DOCUMENT_ID_PATTERN.search(content)
        if match is None:
            raise ValueError(f"Policy {path.name} is missing document_id")
        policy_texts[match.group(1)] = content
    return policy_texts


def run_evaluation(
    *,
    base_url: str,
    cases: Sequence[EvaluationCase],
    policy_texts: Mapping[str, str],
    timeout_seconds: float,
) -> dict[str, object]:
    case_reports: list[dict[str, object]] = []
    successful_requests = 0
    retrieval_hits = 0
    retrieval_expected = 0
    valid_citations = 0
    total_citations = 0
    correct_tools = 0
    required_facts_present = 0
    forbidden_facts_absent = 0

    for index, case in enumerate(cases, start=1):
        status_code, response, error = _post_chat(
            base_url=base_url,
            session_id=f"eval-{index:02d}",
            message=case.message,
            timeout_seconds=timeout_seconds,
        )
        request_successful = status_code == 200 and response is not None
        if request_successful:
            successful_requests += 1
            assert response is not None
            result = evaluate_case(
                expected_document_ids=case.expected_document_ids,
                expected_tools=case.expected_tools,
                required_facts=case.required_facts,
                forbidden_facts=case.forbidden_facts,
                response=response,
                policy_texts=policy_texts,
            )
        else:
            result = CaseResult(
                retrieval_hit=False,
                retrieval_hits=0,
                retrieval_expected=len(case.expected_document_ids),
                citations_valid=False,
                valid_citations=0,
                total_citations=0,
                tools_correct=False,
                required_facts_present=False,
                forbidden_facts_absent=False,
            )

        retrieval_hits += result.retrieval_hits
        retrieval_expected += result.retrieval_expected
        valid_citations += result.valid_citations
        total_citations += result.total_citations
        correct_tools += int(result.tools_correct)
        required_facts_present += int(result.required_facts_present)
        forbidden_facts_absent += int(result.forbidden_facts_absent)
        case_reports.append(
            {
                "id": case.case_id,
                "category": case.category,
                "status_code": status_code,
                "request_successful": request_successful,
                "error": error,
                **asdict(result),
            }
        )

    total = len(cases)
    summary = {
        "total_cases": total,
        "successful_requests": successful_requests,
        "successful_request_rate": _ratio(successful_requests, total),
        "retrieval_recall_at_5": _ratio(retrieval_hits, retrieval_expected),
        "citation_validity": _ratio(valid_citations, total_citations),
        "tool_accuracy": _ratio(correct_tools, total),
        "required_fact_accuracy": _ratio(required_facts_present, total),
        "forbidden_fact_accuracy": _ratio(forbidden_facts_absent, total),
    }
    return {"summary": summary, "cases": case_reports}


def _post_chat(
    *, base_url: str, session_id: str, message: str, timeout_seconds: float
) -> tuple[int, dict[str, object] | None, str | None]:
    base_url = normalize_local_base_url(base_url)
    payload = json.dumps(
        {"session_id": session_id, "message": message}, ensure_ascii=False
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
            if not isinstance(body, dict):
                return response.status, None, "Response is not a JSON object"
            return response.status, body, None
    except urllib.error.HTTPError as error:
        return error.code, None, f"HTTP {error.code}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        return 0, None, type(error).__name__


def normalize_local_base_url(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.hostname != "localhost":
        return base_url
    netloc = "127.0.0.1"
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urllib.parse.urlunsplit(parsed._replace(netloc=netloc))


def _mapping_items(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _non_blank_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _citation_is_valid(
    citation: Mapping[str, object], policy_texts: Mapping[str, str]
) -> bool:
    document_id = _non_blank_string(citation.get("document_id"))
    title = _non_blank_string(citation.get("title"))
    section = _non_blank_string(citation.get("section"))
    quote = _non_blank_string(citation.get("quote"))
    if None in (document_id, title, section, quote):
        return False

    assert document_id is not None
    assert title is not None
    assert section is not None
    assert quote is not None
    policy_text = policy_texts.get(document_id, "")
    title_match = re.search(r"^#\s+(.+?)\s*$", policy_text, re.MULTILINE)
    if title_match is None or title_match.group(1).strip() != title:
        return False

    section_match = re.search(
        rf"^##\s+{re.escape(section)}\s*$\n(?P<body>.*?)(?=^##\s+|\Z)",
        policy_text,
        re.MULTILINE | re.DOTALL,
    )
    return section_match is not None and quote in section_match.group("body")


def _required_string(item: Mapping[str, Any], key: str) -> str:
    value = _non_blank_string(item.get(key))
    if value is None:
        raise ValueError(f"Evaluation case field {key!r} must be a non-blank string")
    return value


def _string_list(item: Mapping[str, Any], key: str) -> list[str]:
    value = item.get(key)
    if not isinstance(value, list) or not all(
        isinstance(entry, str) for entry in value
    ):
        raise ValueError(f"Evaluation case field {key!r} must be a string list")
    return value


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


def _meets_thresholds(summary: Mapping[str, object]) -> bool:
    return (
        float(summary["successful_request_rate"]) == 1.0
        and float(summary["retrieval_recall_at_5"]) >= 0.80
        and float(summary["citation_validity"]) >= 0.90
        and float(summary["tool_accuracy"]) == 1.0
        and float(summary["required_fact_accuracy"]) == 1.0
        and float(summary["forbidden_fact_accuracy"]) == 1.0
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the offline MVP acceptance evaluation"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--questions", type=Path, default=Path("eval/questions.json"))
    parser.add_argument("--policy-dir", type=Path, default=Path("knowledge/policies"))
    parser.add_argument("--output", type=Path, default=Path("eval/report.json"))
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    cases = load_cases(args.questions)
    report = run_evaluation(
        base_url=args.base_url,
        cases=cases,
        policy_texts=load_policy_texts(args.policy_dir),
        timeout_seconds=args.timeout,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    summary = report["summary"]
    assert isinstance(summary, dict)
    print(
        f"successful requests: {summary['successful_requests']}/{summary['total_cases']}"
    )
    print(f"retrieval Recall@5: {summary['retrieval_recall_at_5']:.2%}")
    print(f"citation validity: {summary['citation_validity']:.2%}")
    print(f"tool accuracy: {summary['tool_accuracy']:.2%}")
    print(f"required fact accuracy: {summary['required_fact_accuracy']:.2%}")
    print(f"forbidden fact accuracy: {summary['forbidden_fact_accuracy']:.2%}")
    print(f"report: {args.output}")
    return 0 if _meets_thresholds(summary) else 1


if __name__ == "__main__":
    sys.exit(main())
