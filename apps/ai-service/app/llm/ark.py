import time
from typing import Any

import httpx

from app.llm.contracts import LLMRequest, LLMResult
from app.llm.errors import (
    ProviderBadResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    raise_for_provider_status,
)


class ArkResponsesProvider:
    provider_name = "ark"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def generate(self, request: LLMRequest) -> LLMResult:
        started = time.perf_counter()
        payload: dict[str, Any] = {
            "model": self.model,
            "input": [
                {"role": message.role, "content": message.content} for message in request.messages
            ],
            "temperature": request.temperature,
            "max_output_tokens": request.max_tokens,
        }
        response = await self._post(payload)
        raise_for_provider_status(response)
        try:
            body = response.json()
            content = _extract_output_text(body)
            usage = body.get("usage", {})
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise ProviderBadResponseError from error
        if not content:
            raise ProviderBadResponseError
        return LLMResult(
            content=content,
            provider=self.provider_name,
            model=self.model,
            latency_ms=round((time.perf_counter() - started) * 1000),
            input_tokens=_optional_int(usage.get("input_tokens")),
            output_tokens=_optional_int(usage.get("output_tokens")),
        )

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            if self._client is not None:
                return await self._client.post(
                    f"{self.base_url}/responses",
                    json=payload,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                return await client.post(
                    f"{self.base_url}/responses",
                    json=payload,
                    headers=headers,
                )
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError from error
        except httpx.RequestError as error:
            raise ProviderUnavailableError from error


def _extract_output_text(body: dict[str, Any]) -> str:
    direct = body.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    parts: list[str] = []
    for output in body.get("output", []):
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []):
            if not isinstance(content, dict) or content.get("type") != "output_text":
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts)


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) else None
