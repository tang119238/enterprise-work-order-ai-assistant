import pytest

from app.llm.contracts import LLMMessage, LLMRequest
from app.llm.offline import OfflineTemplateProvider


@pytest.mark.asyncio
async def test_offline_provider_returns_trusted_fallback_text() -> None:
    result = await OfflineTemplateProvider().generate(
        LLMRequest(
            messages=(LLMMessage(role="user", content="问题"),),
            fallback_text="可信答案",
        )
    )

    assert result.content == "可信答案"
    assert result.provider == "offline"
    assert result.model == "deterministic-template"
    assert result.fallback is False
