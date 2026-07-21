import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace

from app.llm.contracts import (
    LLMProvider,
    LLMRequest,
    LLMResult,
    StructuredLLMRequest,
    StructuredLLMResult,
)
from app.llm.errors import ProviderBadResponseError, ProviderError


class LLMGateway:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        fallback_provider: LLMProvider,
        max_retries: int,
        fallback_enabled: bool,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.provider = provider
        self.fallback_provider = fallback_provider
        self.max_retries = max(0, max_retries)
        self.fallback_enabled = fallback_enabled
        self.sleep = sleep

    async def generate(self, request: LLMRequest) -> LLMResult:
        try:
            return await self._generate_primary(request)
        except ProviderError as error:
            if not self.fallback_enabled:
                raise
            fallback = await self.fallback_provider.generate(request)
            return replace(fallback, fallback=True, error_code=error.code)

    async def generate_structured(
        self,
        request: StructuredLLMRequest,
    ) -> StructuredLLMResult:
        # A template fallback is never a valid quality decision. Preserve the
        # provider error so the quality worker can apply its retry policy.
        result = await self._generate_primary(request.as_llm_request())
        try:
            payload = json.loads(result.content)
        except (TypeError, ValueError) as error:
            raise ProviderBadResponseError from error
        if not isinstance(payload, dict):
            raise ProviderBadResponseError
        return StructuredLLMResult(
            payload=payload,
            raw_content=result.content,
            provider=result.provider,
            model=result.model,
            latency_ms=result.latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost=result.estimated_cost,
        )

    async def _generate_primary(self, request: LLMRequest) -> LLMResult:
        last_error: ProviderError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await self.provider.generate(request)
            except ProviderError as error:
                last_error = error
                if not error.retryable or attempt >= self.max_retries:
                    break
                await self.sleep(0.25 * (2**attempt))

        if last_error is None:
            raise RuntimeError("Provider failed without a standardized error")
        raise last_error
