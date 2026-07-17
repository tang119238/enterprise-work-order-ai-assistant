from app.knowledge.bm25 import BM25PolicyIndex
from app.knowledge.models import PolicyChunk


def test_bm25_returns_rework_policy_for_rework_question() -> None:
    policy_index = BM25PolicyIndex(
        [
            PolicyChunk(
                chunk_id="rework-policy:3.2:0",
                document_id="rework-policy",
                title="返工规则",
                section="3.2 返工链路",
                text="返工单必须关联根工单，并保留返工原因。",
            ),
            PolicyChunk(
                chunk_id="sla-policy:2.1:0",
                document_id="sla-policy",
                title="时限规则",
                section="2.1 紧急工单",
                text="紧急工单应在两小时内完成处理。",
            ),
        ]
    )

    hits = policy_index.search("返工单与原工单如何关联", limit=5)

    assert hits[0].document_id == "rework-policy"
    assert hits[0].score > 0


def test_bm25_returns_empty_for_unrelated_question() -> None:
    policy_index = BM25PolicyIndex(
        [
            PolicyChunk(
                chunk_id="sla-policy:2.1:0",
                document_id="sla-policy",
                title="时限规则",
                section="2.1 紧急工单",
                text="紧急工单应在两小时内完成处理。",
            )
        ]
    )

    assert policy_index.search("量子计算价格", limit=5) == []
