import json
from pathlib import Path

import pytest
from eval.run_eval import _meets_thresholds, evaluate_case, normalize_local_base_url
from eval.run_retrieval_eval import (
    RetrievalCase,
    evaluate_retrieval,
    load_retrieval_cases,
    retrieval_meets_thresholds,
)


def test_localhost_is_normalized_to_ipv4_loopback() -> None:
    assert normalize_local_base_url("http://localhost:8000") == "http://127.0.0.1:8000"
    assert normalize_local_base_url("https://example.com") == "https://example.com"


def test_evaluator_calculates_required_metrics() -> None:
    result = evaluate_case(
        expected_document_ids={"rework-policy"},
        expected_tools={"get_rework_chain"},
        required_facts={"WO-20260718-008"},
        forbidden_facts={"WO-20260718-999"},
        response={
            "citations": [
                {
                    "document_id": "rework-policy",
                    "title": "返工处理规则",
                    "section": "3.2 返工链路",
                    "quote": "返工单必须关联根工单。",
                }
            ],
            "tool_calls": [{"name": "get_rework_chain", "status": "success"}],
            "answer": "WO-20260718-008 是返工单，必须关联根工单。",
        },
        policy_texts={
            "rework-policy": ("# 返工处理规则\n\n## 3.2 返工链路\n\n返工单必须关联根工单。")
        },
    )

    assert result.retrieval_hit is True
    assert result.citations_valid is True
    assert result.tools_correct is True
    assert result.required_facts_present is True
    assert result.forbidden_facts_absent is True


def test_evaluator_detects_unsupported_citation_and_wrong_tool() -> None:
    result = evaluate_case(
        expected_document_ids={"sla-policy"},
        expected_tools={"get_work_order"},
        required_facts={"WO-20260718-001"},
        forbidden_facts=set(),
        response={
            "citations": [{"document_id": "sla-policy", "quote": "并不存在的制度原文"}],
            "tool_calls": [{"name": "search_work_orders", "status": "success"}],
            "answer": "未找到指定工单。",
        },
        policy_texts={"sla-policy": "高优先级工单应在四小时内响应。"},
    )

    assert result.retrieval_hit is True
    assert result.citations_valid is False
    assert result.tools_correct is False
    assert result.required_facts_present is False


def test_evaluator_rejects_wrong_citation_metadata_even_when_quote_exists() -> None:
    result = evaluate_case(
        expected_document_ids={"rework-policy"},
        expected_tools=set(),
        required_facts=set(),
        forbidden_facts=set(),
        response={
            "citations": [
                {
                    "document_id": "rework-policy",
                    "title": "错误标题",
                    "section": "3.4 幂等与审计",
                    "quote": "返工单必须关联根工单。",
                }
            ],
            "tool_calls": [],
            "answer": "返工单必须关联根工单。",
        },
        policy_texts={
            "rework-policy": (
                "# 返工处理规则\n\n"
                "## 3.2 返工链路\n\n"
                "返工单必须关联根工单。\n\n"
                "## 3.4 幂等与审计\n\n"
                "同一次验收失败只能创建一张有效返工单。"
            )
        },
    )

    assert result.citations_valid is False


def test_acceptance_thresholds_require_required_and_forbidden_fact_accuracy() -> None:
    passing_summary = {
        "successful_request_rate": 1.0,
        "retrieval_recall_at_5": 0.8,
        "citation_validity": 0.9,
        "tool_accuracy": 1.0,
        "required_fact_accuracy": 1.0,
        "forbidden_fact_accuracy": 1.0,
    }

    assert _meets_thresholds(passing_summary) is True
    assert _meets_thresholds({**passing_summary, "required_fact_accuracy": 0.9667}) is False
    assert _meets_thresholds({**passing_summary, "forbidden_fact_accuracy": 0.9667}) is False


POLICIES = {
    "rework-policy": ("# 返工处理规则\n\n## 3.2 返工链路\n\n返工单必须关联根工单。"),
    "sla-policy": ("# 工单优先级与时限规则\n\n## 2.2 处理时限\n\n紧急工单目标处理时限为两小时。"),
}


def test_hybrid_evaluator_calculates_baseline_quality_and_degradation() -> None:
    cases = [
        RetrievalCase(
            case_id="positive-rework",
            query="再次没修好怎么办",
            kind="positive",
            challenge="synonym",
            expected_document_ids=frozenset({"rework-policy"}),
        ),
        RetrievalCase(
            case_id="positive-sla",
            query="着急的活多久要完成",
            kind="positive",
            challenge="conversational",
            expected_document_ids=frozenset({"sla-policy"}),
        ),
        RetrievalCase(
            case_id="negative-menu",
            query="今天食堂供应什么",
            kind="hard_negative",
            challenge="hard_negative",
            expected_document_ids=frozenset(),
        ),
    ]
    baseline = {
        "positive-rework": ["rework-policy"],
        "positive-sla": [],
        "negative-menu": [],
    }
    hybrid = {
        "positive-rework": {
            "retrieval_mode": "hybrid",
            "warnings": [],
            "citations": [
                {
                    "document_id": "rework-policy",
                    "title": "返工处理规则",
                    "section": "3.2 返工链路",
                    "quote": "返工单必须关联根工单。",
                }
            ],
        },
        "positive-sla": {
            "retrieval_mode": "bm25",
            "warnings": ["HYBRID_RETRIEVAL_DEGRADED"],
            "citations": [
                {
                    "document_id": "sla-policy",
                    "title": "工单优先级与时限规则",
                    "section": "2.2 处理时限",
                    "quote": "紧急工单目标处理时限为两小时。",
                }
            ],
        },
        "negative-menu": {
            "retrieval_mode": "hybrid",
            "warnings": [],
            "citations": [],
        },
    }

    report = evaluate_retrieval(
        cases=cases,
        baseline_document_ids=baseline,
        hybrid_responses=hybrid,
        policy_texts=POLICIES,
    )

    assert report["bm25_recall_at_5"] == 0.5
    assert report["hybrid_recall_at_5"] == 1.0
    assert report["citation_validity"] == 1.0
    assert report["hard_negative_accuracy"] == 1.0
    assert report["degraded_request_count"] == 1


def test_retrieval_thresholds_require_hybrid_not_below_baseline() -> None:
    passing = {
        "bm25_recall_at_5": 0.9,
        "hybrid_recall_at_5": 0.9,
        "citation_validity": 0.95,
        "hard_negative_accuracy": 1.0,
        "successful_request_rate": 1.0,
    }

    assert retrieval_meets_thresholds(passing) is True
    assert retrieval_meets_thresholds({**passing, "hybrid_recall_at_5": 0.89}) is False
    assert retrieval_meets_thresholds({**passing, "citation_validity": 0.949}) is False


def test_retrieval_dataset_contract_rejects_unknown_and_invalid_cases(
    tmp_path: Path,
) -> None:
    cases = [
        {
            "id": f"positive-{index}",
            "query": f"问题 {index}",
            "kind": "positive",
            "challenge": ["synonym", "conversational", "keyword_absent"][index % 3],
            "expected_document_ids": ["rework-policy"],
        }
        for index in range(30)
    ]
    cases[-1]["expected_document_ids"] = ["unknown-policy"]
    path = tmp_path / "questions.json"
    path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="current synthetic document"):
        load_retrieval_cases(path, current_document_ids=set(POLICIES))

    cases[-1] = {
        "id": "negative",
        "query": "无关问题",
        "kind": "hard_negative",
        "challenge": "hard_negative",
        "expected_document_ids": ["rework-policy"],
    }
    path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="hard negative"):
        load_retrieval_cases(path, current_document_ids=set(POLICIES))


def test_checked_in_hybrid_dataset_has_required_coverage() -> None:
    cases = load_retrieval_cases(
        Path("eval/hybrid_questions.json"),
        current_document_ids={"rework-policy", "sla-policy", "work-order-lifecycle"},
    )

    assert len(cases) >= 30
    assert {case.challenge for case in cases} >= {
        "synonym",
        "conversational",
        "keyword_absent",
        "hard_negative",
    }
    assert all(
        case.expected_document_ids <= {"rework-policy", "sla-policy", "work-order-lifecycle"}
        for case in cases
        if case.kind == "positive"
    )
    assert all(not case.expected_document_ids for case in cases if case.kind == "hard_negative")
