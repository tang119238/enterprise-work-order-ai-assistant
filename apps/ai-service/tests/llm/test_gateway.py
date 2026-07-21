from collections.abc import Callable

import pytest

from app.llm.contracts import (
    LLMMessage,
    LLMRequest,
    LLMResult,
    StructuredLLMRequest,
)
from app.llm.errors import ProviderAuthError, ProviderBadResponseError, ProviderTimeoutError
from app.llm.gateway import LLMGateway
from app.llm.offline import OfflineTemplateProvider


class FailingProvider:
    def __init__(self, error_factory: Callable[[], Exception]) -> None:
        self.error_factory = error_factory
        self.attempts = 0

    async def generate(self, request: LLMRequest) -> LLMResult:
        self.attempts += 1
        raise self.error_factory()


async def no_wait(_: float) -> None:
    return None


@pytest.mark.asyncio
async def test_gateway_retries_timeout_then_returns_visible_fallback() -> None:
    provider = FailingProvider(lambda: ProviderTimeoutError())
    gateway = LLMGateway(
        provider=provider,
        fallback_provider=OfflineTemplateProvider(),
        max_retries=2,
        fallback_enabled=True,
        sleep=no_wait,
    )

    result = await gateway.generate(request())

    assert provider.attempts == 3
    assert result.content == "可信离线答案"
    assert result.fallback is True
    assert result.error_code == "PROVIDER_TIMEOUT"


@pytest.mark.asyncio
async def test_gateway_does_not_retry_authentication_failure() -> None:
    provider = FailingProvider(lambda: ProviderAuthError())
    gateway = LLMGateway(
        provider=provider,
        fallback_provider=OfflineTemplateProvider(),
        max_retries=2,
        fallback_enabled=True,
        sleep=no_wait,
    )

    result = await gateway.generate(request())

    assert provider.attempts == 1
    assert result.fallback is True
    assert result.error_code == "PROVIDER_AUTH_FAILED"


@pytest.mark.asyncio
async def test_gateway_raises_when_fallback_is_disabled() -> None:
    provider = FailingProvider(lambda: ProviderAuthError())
    gateway = LLMGateway(
        provider=provider,
        fallback_provider=OfflineTemplateProvider(),
        max_retries=2,
        fallback_enabled=False,
        sleep=no_wait,
    )

    with pytest.raises(ProviderAuthError):
        await gateway.generate(request())


def request() -> LLMRequest:
    return LLMRequest(
        messages=(LLMMessage(role="user", content="问题"),),
        fallback_text="可信离线答案",
    )


class StructuredProvider:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResult:
        self.requests.append(request)
        return LLMResult(
            content=self.content,
            provider="synthetic",
            model="structured-model",
            latency_ms=11,
        )


def structured_request() -> StructuredLLMRequest:
    return StructuredLLMRequest(
        messages=(LLMMessage(role="user", content="return json"),),
        response_schema={"type": "object"},
        prompt_version="v1",
        request_id="request-1",
    )


@pytest.mark.asyncio
async def test_generate_structured_decodes_object_and_passes_schema_metadata() -> None:
    provider = StructuredProvider('{"verdict":"PASS"}')
    gateway = LLMGateway(
        provider=provider,
        fallback_provider=OfflineTemplateProvider(),
        max_retries=0,
        fallback_enabled=True,
    )

    result = await gateway.generate_structured(structured_request())

    assert result.payload == {"verdict": "PASS"}
    assert result.raw_content == '{"verdict":"PASS"}'
    assert provider.requests[0].response_schema == {"type": "object"}
    assert provider.requests[0].request_id == "request-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["not-json", "[]", "null"])
async def test_generate_structured_rejects_non_object_json(content: str) -> None:
    gateway = LLMGateway(
        provider=StructuredProvider(content),
        fallback_provider=OfflineTemplateProvider(),
        max_retries=0,
        fallback_enabled=True,
    )

    with pytest.raises(ProviderBadResponseError):
        await gateway.generate_structured(structured_request())


@pytest.mark.asyncio
async def test_generate_structured_never_uses_template_fallback() -> None:
    provider = FailingProvider(lambda: ProviderTimeoutError())
    gateway = LLMGateway(
        provider=provider,
        fallback_provider=OfflineTemplateProvider(),
        max_retries=0,
        fallback_enabled=True,
    )

    with pytest.raises(ProviderTimeoutError):
        await gateway.generate_structured(structured_request())
