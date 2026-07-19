from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.knowledge.bm25 import BM25PolicyIndex
from app.knowledge.loader import load_policy_directory
from eval.run_eval import evaluate_case, load_policy_texts, normalize_local_base_url

REQUIRED_CHALLENGES = frozenset(
    {"synonym", "conversational", "keyword_absent", "hard_negative"}
)
DEGRADED_WARNING = "HYBRID_RETRIEVAL_DEGRADED"


@dataclass(frozen=True)
class RetrievalCase:
    case_id: str
    query: str
    kind: Literal["positive", "hard_negative"]
    challenge: Literal["synonym", "conversational", "keyword_absent", "hard_negative"]
    expected_document_ids: frozenset[str]


def load_retrieval_cases(
    path: Path,
    *,
    current_document_ids: set[str] | frozenset[str],
) -> list[RetrievalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or len(raw) < 30:
        raise ValueError("Retrieval evaluation must contain at least 30 cases")
    cases: list[RetrievalCase] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Every retrieval case must be an object")
        kind = _required_string(item, "kind")
        challenge = _required_string(item, "challenge")
        if kind not in {"positive", "hard_negative"}:
            raise ValueError("Retrieval case kind is invalid")
        if challenge not in REQUIRED_CHALLENGES:
            raise ValueError("Retrieval case challenge is invalid")
        expected = frozenset(_string_list(item, "expected_document_ids"))
        if kind == "positive":
            if not expected or not expected <= current_document_ids:
                raise ValueError(
                    "Every positive case must name a current synthetic document"
                )
            if challenge == "hard_negative":
                raise ValueError("A positive case cannot be a hard negative")
        elif expected:
            raise ValueError("Every hard negative must expect no citation")
        elif challenge != "hard_negative":
            raise ValueError("Every hard negative must use the hard_negative challenge")
        cases.append(
            RetrievalCase(
                case_id=_required_string(item, "id"),
                query=_required_string(item, "query"),
                kind=kind,
                challenge=challenge,
                expected_document_ids=expected,
            )
        )
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("Retrieval case ids must be unique")
    if {case.challenge for case in cases} < REQUIRED_CHALLENGES:
        raise ValueError("Retrieval cases must cover every required challenge")
    return cases


def evaluate_retrieval(
    *,
    cases: Sequence[RetrievalCase],
    baseline_document_ids: Mapping[str, Sequence[str]],
    hybrid_responses: Mapping[str, Mapping[str, object] | None],
    policy_texts: Mapping[str, str],
) -> dict[str, float | int]:
    expected_total = 0
    baseline_hits = 0
    hybrid_hits = 0
    valid_citations = 0
    total_citations = 0
    hard_negative_total = 0
    hard_negative_passed = 0
    degraded = 0
    successful = 0

    for case in cases:
        baseline_ids = set(baseline_document_ids.get(case.case_id, ()))
        response = hybrid_responses.get(case.case_id)
        request_succeeded = response is not None
        if request_succeeded:
            successful += 1
        else:
            response = {}
        citations = _mapping_items(response.get("citations"))
        hybrid_ids = {
            document_id
            for citation in citations
            if isinstance((document_id := citation.get("document_id")), str)
        }
        if case.kind == "positive":
            expected_total += len(case.expected_document_ids)
            baseline_hits += len(case.expected_document_ids & baseline_ids)
            hybrid_hits += len(case.expected_document_ids & hybrid_ids)
        else:
            hard_negative_total += 1
            hard_negative_passed += int(request_succeeded and not citations)

        result = evaluate_case(
            expected_document_ids=case.expected_document_ids,
            expected_tools=frozenset(),
            required_facts=frozenset(),
            forbidden_facts=frozenset(),
            response=response,
            policy_texts=policy_texts,
        )
        valid_citations += result.valid_citations
        total_citations += result.total_citations
        warnings = response.get("warnings")
        if isinstance(warnings, list) and DEGRADED_WARNING in warnings:
            degraded += 1

    return {
        "total_cases": len(cases),
        "successful_request_rate": _ratio(successful, len(cases)),
        "bm25_recall_at_5": _ratio(baseline_hits, expected_total),
        "hybrid_recall_at_5": _ratio(hybrid_hits, expected_total),
        "citation_validity": _ratio(valid_citations, total_citations),
        "hard_negative_accuracy": _ratio(
            hard_negative_passed,
            hard_negative_total,
        ),
        "degraded_request_count": degraded,
    }


def retrieval_meets_thresholds(summary: Mapping[str, object]) -> bool:
    baseline = float(summary["bm25_recall_at_5"])
    hybrid = float(summary["hybrid_recall_at_5"])
    return (
        float(summary["successful_request_rate"]) == 1.0
        and hybrid >= baseline
        and hybrid >= 0.90
        and float(summary["citation_validity"]) >= 0.95
        and float(summary["hard_negative_accuracy"]) == 1.0
    )


def run_live_evaluation(
    *,
    base_url: str,
    token: str,
    cases: Sequence[RetrievalCase],
    policy_texts: Mapping[str, str],
    policy_directory: Path,
    timeout_seconds: float,
) -> dict[str, float | int]:
    if not token.strip():
        raise ValueError("A bearer token is required for retrieval evaluation")
    baseline_index = BM25PolicyIndex(load_policy_directory(policy_directory))
    baseline = {
        case.case_id: [
            hit.document_id for hit in baseline_index.search(case.query, limit=5)
        ]
        for case in cases
    }
    hybrid = {
        case.case_id: _post_chat(
            base_url=base_url,
            token=token,
            session_id=f"retrieval-eval-{index:02d}",
            message=case.query,
            timeout_seconds=timeout_seconds,
        )
        for index, case in enumerate(cases, start=1)
    }
    return evaluate_retrieval(
        cases=cases,
        baseline_document_ids=baseline,
        hybrid_responses=hybrid,
        policy_texts=policy_texts,
    )


def _post_chat(
    *,
    base_url: str,
    token: str,
    session_id: str,
    message: str,
    timeout_seconds: float,
) -> Mapping[str, object] | None:
    payload = json.dumps(
        {"session_id": session_id, "message": message},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{normalize_local_base_url(base_url).rstrip('/')}/chat",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
            return body if response.status == 200 and isinstance(body, dict) else None
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
    ):
        return None


def _mapping_items(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _required_string(item: Mapping[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Retrieval case field {key!r} must be nonblank")
    return value.strip()


def _string_list(item: Mapping[str, Any], key: str) -> list[str]:
    value = item.get(key)
    if not isinstance(value, list) or not all(
        isinstance(entry, str) and entry.strip() for entry in value
    ):
        raise ValueError(f"Retrieval case field {key!r} must be a string list")
    return [entry.strip() for entry in value]


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


def _token_from_env_file(path: Path) -> str:
    if not path.is_file():
        return ""
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name in {"EVAL_BEARER_TOKEN", "SMOKE_DISPATCHER_TOKEN"}:
            values[name] = value.strip()
    return values.get("EVAL_BEARER_TOKEN") or values.get("SMOKE_DISPATCHER_TOKEN", "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate tenant hybrid retrieval")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default=os.getenv("EVAL_BEARER_TOKEN", ""))
    parser.add_argument("--env-file", type=Path, default=Path(".smoke/smoke.env"))
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path("eval/hybrid_questions.json"),
    )
    parser.add_argument("--policy-dir", type=Path, default=Path("knowledge/policies"))
    parser.add_argument("--output", type=Path, default=Path("eval/hybrid_report.json"))
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    policy_texts = load_policy_texts(args.policy_dir)
    cases = load_retrieval_cases(
        args.questions,
        current_document_ids=set(policy_texts),
    )
    summary = run_live_evaluation(
        base_url=args.base_url,
        token=args.token or _token_from_env_file(args.env_file),
        cases=cases,
        policy_texts=policy_texts,
        policy_directory=args.policy_dir,
        timeout_seconds=args.timeout,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"BM25 Recall@5: {summary['bm25_recall_at_5']:.2%}")
    print(f"hybrid Recall@5: {summary['hybrid_recall_at_5']:.2%}")
    print(f"citation validity: {summary['citation_validity']:.2%}")
    print(f"hard-negative accuracy: {summary['hard_negative_accuracy']:.2%}")
    print(f"degraded requests: {summary['degraded_request_count']}")
    print(f"report: {args.output}")
    return 0 if retrieval_meets_thresholds(summary) else 1


if __name__ == "__main__":
    sys.exit(main())
