from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping


def request_json(
    url: str,
    *,
    payload: Mapping[str, str] | None = None,
    timeout_seconds: float = 5.0,
) -> dict[str, object]:
    data = None
    headers: dict[str, str] = {}
    method = "GET"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
        method = "POST"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise AssertionError(f"Expected JSON object from {url}")
    return body


def wait_until_ready(url: str, wait_seconds: float) -> None:
    deadline = time.monotonic() + wait_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = request_json(url, timeout_seconds=2.0)
            if response.get("status") in {"UP", "ok"}:
                return
        except (
            OSError,
            TimeoutError,
            urllib.error.URLError,
            json.JSONDecodeError,
        ) as error:
            last_error = error
        time.sleep(1.0)
    raise TimeoutError(f"Service did not become ready: {url}; last error: {last_error}")


def _tool_names(response: Mapping[str, object]) -> set[str]:
    calls = response.get("tool_calls")
    if not isinstance(calls, list):
        return set()
    return {
        name
        for call in calls
        if isinstance(call, dict)
        and call.get("status") == "success"
        and isinstance((name := call.get("name")), str)
    }


def _document_ids(response: Mapping[str, object]) -> set[str]:
    citations = response.get("citations")
    if not isinstance(citations, list):
        return set()
    return {
        document_id
        for citation in citations
        if isinstance(citation, dict)
        and isinstance((document_id := citation.get("document_id")), str)
    }


def run_smoke_tests(
    ai_base_url: str, work_order_base_url: str, wait_seconds: float
) -> None:
    wait_until_ready(f"{work_order_base_url}/actuator/health", wait_seconds)
    wait_until_ready(f"{ai_base_url}/health", wait_seconds)

    order = request_json(f"{work_order_base_url}/api/work-orders/WO-20260718-008")
    assert order["root_work_order_no"] == "WO-20260718-007"
    assert order["status"] == "PROCESSING"

    page = request_json(
        f"{work_order_base_url}/api/work-orders?status=PROCESSING&size=20"
    )
    assert page["total"] == 10
    assert len(page["items"]) == 10

    knowledge = request_json(
        f"{ai_base_url}/chat",
        payload={"session_id": "smoke-k", "message": "返工链路规则是什么？"},
    )
    assert _tool_names(knowledge) == set()
    assert "rework-policy" in _document_ids(knowledge)

    work_order = request_json(
        f"{ai_base_url}/chat",
        payload={"session_id": "smoke-w", "message": "查询 WO-20260718-001 当前状态"},
    )
    assert _tool_names(work_order) == {"get_work_order"}
    assert "WO-20260718-001" in str(work_order["answer"])

    combined = request_json(
        f"{ai_base_url}/chat",
        payload={
            "session_id": "smoke-c",
            "message": "WO-20260718-008 为什么是返工单，接下来怎么处理？",
        },
    )
    assert _tool_names(combined) == {"get_rework_chain"}
    assert "rework-policy" in _document_ids(combined)
    assert "WO-20260718-007" in str(combined["answer"])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test the local Docker Compose stack"
    )
    parser.add_argument("--ai-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--work-order-base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--wait-seconds", type=float, default=180.0)
    args = parser.parse_args()

    run_smoke_tests(args.ai_base_url, args.work_order_base_url, args.wait_seconds)
    print("smoke tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
