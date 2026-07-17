import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.llm.contracts import LLMRequest, LLMResult
from app.llm.errors import (
    ProviderBadResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    raise_for_provider_status,
)


@dataclass(frozen=True)
class OpenAICompatibleCapabilities:
    supports_temperature: bool = True


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        provider_name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        capabilities: OpenAICompatibleCapabilities | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.capabilities = capabilities or OpenAICompatibleCapabilities()
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def generate(self, request: LLMRequest) -> LLMResult:
        started = time.perf_counter()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": message.role, "content": message.content} for message in request.messages
            ],
            "max_tokens": request.max_tokens,
        }
        if self.capabilities.supports_temperature:
            payload["temperature"] = request.temperature
        response = await self._post(payload)
        raise_for_provider_status(response)
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            usage = body.get("usage", {})
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise ProviderBadResponseError from error
        if not isinstance(content, str) or not content.strip():
            raise ProviderBadResponseError
        return LLMResult(
            content=content.strip(),
            provider=self.provider_name,
            model=self.model,
            latency_ms=round((time.perf_counter() - started) * 1000),
            input_tokens=_optional_int(usage.get("prompt_tokens")),
            output_tokens=_optional_int(usage.get("completion_tokens")),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            return await self._client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError from error
        except httpx.RequestError as error:
            raise ProviderUnavailableError from error


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) else None
