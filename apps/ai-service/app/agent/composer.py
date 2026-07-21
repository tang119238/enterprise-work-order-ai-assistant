from app.api.models import Citation, WorkOrderRecord
from app.knowledge.models import SearchHit


def format_work_order_facts(records: list[WorkOrderRecord]) -> str:
    if not records:
        return ""
    lines = []
    for record in records:
        parts = [f"工单号 {record.work_order_no}"]
        parts.append(f"状态 {record.status}")
        parts.append(f"优先级 {record.priority}")
        if record.assignee_name:
            parts.append(f"负责人 {record.assignee_name}")
        if record.due_at:
            parts.append(f"截止时间 {record.due_at.strftime('%Y-%m-%d %H:%M')}")
        if record.root_work_order_no:
            parts.append(f"根工单 {record.root_work_order_no}")
        if record.rework_reason:
            parts.append(f"返工原因 {record.rework_reason}")
        lines.append("；".join(parts))
    return "\n".join(lines)


def format_policy_fallback(hits: list[SearchHit]) -> str:
    if not hits:
        return ""
    return "\n".join(f"根据《{hit.title}》{hit.section}：{hit.text}" for hit in hits[:2])


def build_citations(hits: list[SearchHit]) -> list[Citation]:
    return [
        Citation(
            document_id=hit.document_id,
            title=hit.title,
            section=hit.section,
            quote=hit.text,
        )
        for hit in hits[:5]
    ]
