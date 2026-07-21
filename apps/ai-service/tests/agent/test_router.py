import pytest

from app.agent.router import extract_search_filters, route_intent


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("返工单创建后如何处理？", "knowledge"),
        ("查询 WO-20260718-001 当前状态", "work_order"),
        ("WO-20260718-008 为什么是返工单，接下来怎么处理？", "combined"),
        ("查询处理中工单", "work_order"),
    ],
)
def test_routes(message: str, expected: str) -> None:
    assert route_intent(message) == expected


def test_default_route_is_knowledge() -> None:
    assert route_intent("验收有什么要求？") == "knowledge"


def test_extract_search_filters_with_assignee() -> None:
    filters = extract_search_filters("林晓的工单有哪些")
    assert filters["assigneeName"] == "林晓"


def test_extract_search_filters_with_status_and_project() -> None:
    filters = extract_search_filters("查询星河中心处理中工单")
    assert filters["status"] == "PROCESSING"
    assert filters["projectName"] == "星河中心"
