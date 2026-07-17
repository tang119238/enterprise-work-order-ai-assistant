import json

import httpx
import pytest

from app.llm.ark import ArkResponsesProvider
from app.llm.contracts import LLMMessage, LLMRequest


@pytest.mark.asyncio
async def test_ark_provider_maps_responses_request_and_output() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://ark.example/api/v3/responses"
        payload = json.loads(request.content)
        assert payload["model"] == "doubao-demo"
        assert payload["input"] == [{"role": "user", "content": "解释时限"}]
        assert payload["max_output_tokens"] == 800
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "高优先级为八小时。"}],
                    }
                ],
                "usage": {"input_tokens": 9, "output_tokens": 6},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ArkResponsesProvider(
            base_url="https://ark.example/api/v3",
            api_key="test-key",
            model="doubao-demo",
            timeout_seconds=5,
            client=client,
        )
        result = await provider.generate(
            LLMRequest(
                messages=(LLMMessage(role="user", content="解释时限"),),
                fallback_text="离线答案",
            )
        )

    assert result.content == "高优先级为八小时。"
    assert result.provider == "ark"
    assert result.input_tokens == 9
    assert result.output_tokens == 6
