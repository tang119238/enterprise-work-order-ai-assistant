from eval.run_eval import evaluate_case, normalize_local_base_url


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
                    "quote": "返工单必须关联根工单。",
                }
            ],
            "tool_calls": [{"name": "get_rework_chain", "status": "success"}],
            "answer": "WO-20260718-008 是返工单，必须关联根工单。",
        },
        policy_texts={"rework-policy": "返工单必须关联根工单。"},
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
