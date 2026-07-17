import httpx
import pytest

from app.agent.graph import AgentDependencies
from app.main import create_app
from tests.agent.test_graph import dependencies


def app_dependencies() -> AgentDependencies:
    return dependencies()


@pytest.mark.asyncio
async def test_chat_contract() -> None:
    transport = httpx.ASGITransport(app=create_app(dependencies=app_dependencies()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/chat",
            json={"session_id": "demo-001", "message": "返工规则是什么？"},
        )

    assert response.status_code == 200
    body = response.json()
    assert {"answer", "citations", "tool_calls", "latency_ms", "model", "warnings"} <= body.keys()


@pytest.mark.asyncio
async def test_chat_rejects_blank_message() -> None:
    transport = httpx.ASGITransport(app=create_app(dependencies=app_dependencies()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/chat", json={"session_id": "demo-001", "message": "   "})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_rejects_message_above_two_thousand_characters() -> None:
    transport = httpx.ASGITransport(app=create_app(dependencies=app_dependencies()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/chat",
            json={"session_id": "demo-001", "message": "问" * 2001},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_health_reports_provider_without_secret() -> None:
    transport = httpx.ASGITransport(app=create_app(dependencies=app_dependencies()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "provider": "offline"}
    assert "key" not in response.text.lower()
