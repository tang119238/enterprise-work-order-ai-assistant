from app.api.models import Citation, WorkOrderRecord
from app.knowledge.models import RetrievalHit


def format_work_order_facts(records: list[WorkOrderRecord]) -> str:
    if not records:
        return ""
    lines = ["工单事实："]
    for record in records:
        fields = [
            f"工单号 {record.work_order_no}",
            f"状态 {record.status}",
            f"优先级 {record.priority}",
            f"负责人 {record.assignee_name or '未分配'}",
            f"截止时间 {record.due_at.isoformat(timespec='seconds')}",
        ]
        if record.root_work_order_no:
            fields.append(f"根工单 {record.root_work_order_no}")
        lines.append("- " + "；".join(fields))
    return "\n".join(lines)


def format_policy_fallback(hits: list[RetrievalHit]) -> str:
    if not hits:
        return "当前知识库没有足够依据回答该问题。"
    return "\n".join(f"根据《{hit.title}》{hit.section}：{hit.text}" for hit in hits[:2])


def build_citations(hits: list[RetrievalHit]) -> list[Citation]:
    return [
        Citation(
            document_id=hit.document_id,
            title=hit.title,
            section=hit.section,
            quote=hit.text,
        )
        for hit in hits[:5]
    ]
