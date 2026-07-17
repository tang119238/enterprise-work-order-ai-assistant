from dataclasses import dataclass
from typing import Literal, Protocol


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


class LLMProvider(Protocol):
    async def generate(self, request: LLMRequest) -> LLMResult: ...
