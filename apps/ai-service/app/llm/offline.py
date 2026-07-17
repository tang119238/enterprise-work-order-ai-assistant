from app.llm.contracts import LLMRequest, LLMResult


class OfflineTemplateProvider:
    provider_name = "offline"

    async def generate(self, request: LLMRequest) -> LLMResult:
        return LLMResult(
            content=request.fallback_text,
            provider=self.provider_name,
            model="deterministic-template",
            latency_ms=0,
        )
