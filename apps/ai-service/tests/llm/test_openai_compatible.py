import json

import httpx
import pytest

from app.llm.contracts import LLMMessage, LLMRequest
from app.llm.errors import ProviderAuthError
from app.llm.openai_compatible import OpenAICompatibleProvider


@pytest.mark.asyncio
async def test_compatible_provider_sends_normalized_chat_request() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://model.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload == {
            "model": "demo-model",
            "messages": [{"role": "user", "content": "解释返工规则"}],
            "temperature": 0.1,
            "max_tokens": 800,
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "返工单必须关联根工单。"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            provider_name="custom",
            base_url="https://model.example/v1",
            api_key="test-key",
            model="demo-model",
            timeout_seconds=5,
            client=client,
        )
        result = await provider.generate(
            LLMRequest(
                messages=(LLMMessage(role="user", content="解释返工规则"),),
                fallback_text="离线答案",
            )
        )

    assert result.content == "返工单必须关联根工单。"
    assert result.provider == "custom"
    assert result.input_tokens == 12
    assert result.output_tokens == 8


@pytest.mark.asyncio
async def test_auth_error_does_not_expose_key_or_response_body() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="server echoed test-key")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            provider_name="deepseek",
            base_url="https://model.example/v1",
            api_key="test-key",
            model="demo-model",
            timeout_seconds=5,
            client=client,
        )
        with pytest.raises(ProviderAuthError) as error:
            await provider.generate(
                LLMRequest(
                    messages=(LLMMessage(role="user", content="问题"),),
                    fallback_text="离线答案",
                )
            )

    assert "test-key" not in str(error.value)

