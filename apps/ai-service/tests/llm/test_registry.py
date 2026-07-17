import pytest

from app.config import Settings
from app.llm.ark import ArkResponsesProvider
from app.llm.offline import OfflineTemplateProvider
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.registry import build_provider


def settings_for(provider: str, *, api_key: str = "test-key") -> Settings:
    return Settings(
        llm_provider=provider,
        llm_api_key=api_key,
        llm_model="test-model",
    )


@pytest.mark.parametrize(
    ("name", "expected_base_url"),
    [
        ("deepseek", "https://api.deepseek.com"),
        ("bailian", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ("zhipu", "https://open.bigmodel.cn/api/paas/v4"),
        ("kimi", "https://api.moonshot.cn/v1"),
        ("qianfan", "https://qianfan.baidubce.com/v2"),
    ],
)
def test_domestic_presets_use_openai_compatible_provider(name: str, expected_base_url: str) -> None:
    provider = build_provider(settings_for(name))

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.provider_name == name
    assert provider.base_url == expected_base_url


def test_ark_uses_dedicated_responses_adapter() -> None:
    provider = build_provider(settings_for("ark"))

    assert isinstance(provider, ArkResponsesProvider)


def test_missing_key_falls_back_to_offline_provider() -> None:
    provider = build_provider(settings_for("deepseek", api_key=""))

    assert isinstance(provider, OfflineTemplateProvider)


def test_custom_provider_requires_explicit_base_url() -> None:
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        build_provider(settings_for("custom"))


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
        build_provider(settings_for("unknown"))
