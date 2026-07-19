from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class LLMMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMRequest:
    messages: tuple[LLMMessage, ...]
    fallback_text: str
    temperature: float = 0.1
    max_tokens: int = 800
    response_schema: Mapping[str, object] | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class LLMResult:
    content: str
    provider: str
    model: str
    latency_ms: int
    fallback: bool = False
    error_code: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None


@dataclass(frozen=True)
class StructuredLLMRequest:
    messages: tuple[LLMMessage, ...]
    response_schema: Mapping[str, object]
    prompt_version: str
    request_id: str
    temperature: float = 0.0
    max_tokens: int = 1600

    def as_llm_request(self) -> LLMRequest:
        return LLMRequest(
            messages=self.messages,
            fallback_text="",
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            response_schema=self.response_schema,
            request_id=self.request_id,
        )


@dataclass(frozen=True)
class StructuredLLMResult:
    payload: Mapping[str, Any]
    raw_content: str
    provider: str
    model: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None


class LLMProvider(Protocol):
    async def generate(self, request: LLMRequest) -> LLMResult: ...
