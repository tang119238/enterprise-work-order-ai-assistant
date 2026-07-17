import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace

from app.llm.contracts import LLMProvider, LLMRequest, LLMResult
from app.llm.errors import ProviderError


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
        if not self.fallback_enabled:
            raise last_error
        fallback = await self.fallback_provider.generate(request)
        return replace(fallback, fallback=True, error_code=last_error.code)
