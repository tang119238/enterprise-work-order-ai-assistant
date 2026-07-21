import re
from typing import Literal

Route = Literal["knowledge", "work_order", "combined"]
WORK_ORDER_NUMBER_PATTERN = re.compile(r"WO-\d{8}-\d{3}", re.IGNORECASE)
ASSIGNEE_PATTERN = re.compile(r"([\u4e00-\u9fa5]{2,3})的工单")
EXPLANATION_TERMS = ("为什么", "怎么处理", "如何处理", "规则", "制度", "要求", "依据")
QUERY_TERMS = ("查询", "查一下", "列出", "有哪些")
STATUS_TERMS = ("待派单", "待接单", "处理中", "已完成", "已关闭", "紧急工单")
KNOWN_PROJECTS = ("星河中心", "云帆园区", "海棠公寓")


def route_intent(message: str) -> Route:
    has_number = WORK_ORDER_NUMBER_PATTERN.search(message) is not None
    if has_number:
        if any(term in message for term in EXPLANATION_TERMS):
            return "combined"
        return "work_order"
    if any(term in message for term in QUERY_TERMS) and (
        "工单" in message or any(term in message for term in STATUS_TERMS)
    ):
        return "work_order"
    return "knowledge"


def extract_work_order_no(message: str) -> str | None:
    match = WORK_ORDER_NUMBER_PATTERN.search(message)
    return match.group(0).upper() if match else None


def extract_search_filters(message: str) -> dict[str, str]:
    filters: dict[str, str] = {}
    status_map = {
        "待派单": "PENDING_DISPATCH",
        "待接单": "PENDING_ACCEPTANCE",
        "处理中": "PROCESSING",
        "已完成": "COMPLETED",
        "已关闭": "CLOSED",
    }
    priority_map = {
        "紧急": "URGENT",
        "高优先级": "HIGH",
        "中优先级": "MEDIUM",
        "低优先级": "LOW",
    }
    for label, value in status_map.items():
        if label in message:
            filters["status"] = value
            break
    for label, value in priority_map.items():
        if label in message:
            filters["priority"] = value
            break
    for project in KNOWN_PROJECTS:
        if project in message:
            filters["projectName"] = project
            break
    assignee_match = ASSIGNEE_PATTERN.search(message)
    if assignee_match:
        filters["assigneeName"] = assignee_match.group(1)
    return filters
