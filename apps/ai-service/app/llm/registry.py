import httpx

from app.config import Settings
from app.llm.ark import ArkResponsesProvider
from app.llm.contracts import LLMProvider
from app.llm.offline import OfflineTemplateProvider
from app.llm.openai_compatible import OpenAICompatibleProvider

OPENAI_COMPATIBLE_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "bailian": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "kimi": "https://api.moonshot.cn/v1",
    "qianfan": "https://qianfan.baidubce.com/v2",
}
ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
SUPPORTED_PROVIDERS = {"offline", "ark", "custom", *OPENAI_COMPATIBLE_BASE_URLS}


def build_provider(
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
) -> LLMProvider:
    provider_name = settings.llm_provider.strip().lower()
    if provider_name not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider_name}")
    api_key = settings.api_key_value()
    if provider_name == "offline" or not api_key:
        return OfflineTemplateProvider()
    if not settings.llm_model.strip():
        raise ValueError("LLM_MODEL is required for online providers")

    if provider_name == "ark":
        return ArkResponsesProvider(
            base_url=settings.llm_base_url or ARK_BASE_URL,
            api_key=api_key,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            client=client,
        )

    if provider_name == "custom" and not settings.llm_base_url:
        raise ValueError("LLM_BASE_URL is required for custom provider")
    base_url = settings.llm_base_url or OPENAI_COMPATIBLE_BASE_URLS[provider_name]
    return OpenAICompatibleProvider(
        provider_name=provider_name,
        base_url=base_url,
        api_key=api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        client=client,
    )
