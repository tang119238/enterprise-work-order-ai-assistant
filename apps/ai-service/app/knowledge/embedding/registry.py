from collections.abc import Callable, Sequence
from pathlib import Path

import httpx

from app.config import Settings
from app.knowledge.embedding.base import (
    EMBEDDING_DIMENSIONS,
    EmbeddingCapabilityError,
    EmbeddingConfigurationError,
    EmbeddingProvider,
)
from app.knowledge.embedding.fastembed_provider import (
    FASTEMBED_MODEL_NAME,
    FastEmbedEmbeddingProvider,
    FastEmbedModel,
)
from app.knowledge.embedding.openai_compatible import (
    OpenAICompatibleEmbeddingProvider,
    valid_embedding_timeout,
)

SUPPORTED_EMBEDDING_PROVIDERS = frozenset({"local", "openai_compatible", "disabled"})


class DisabledEmbeddingProvider:
    @property
    def model_key(self) -> str:
        return "disabled"

    @property
    def dimensions(self) -> int:
        return EMBEDDING_DIMENSIONS

    @property
    def loaded(self) -> bool:
        return False

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingCapabilityError


async def build_embedding_provider(
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
    fastembed_factory: Callable[..., FastEmbedModel] | None = None,
) -> EmbeddingProvider:
    provider_name = settings.embedding_provider
    if provider_name not in SUPPORTED_EMBEDDING_PROVIDERS:
        raise EmbeddingConfigurationError
    if settings.embedding_dimensions != EMBEDDING_DIMENSIONS:
        raise EmbeddingConfigurationError
    if provider_name == "disabled":
        return DisabledEmbeddingProvider()
    if provider_name == "local":
        if settings.embedding_model != FASTEMBED_MODEL_NAME:
            raise EmbeddingConfigurationError
        if not _usable_cache_path(settings.fastembed_cache_path):
            raise EmbeddingConfigurationError
        provider: EmbeddingProvider = FastEmbedEmbeddingProvider(
            cache_path=settings.fastembed_cache_path,
            dimensions=settings.embedding_dimensions,
            model_factory=fastembed_factory,
        )
    else:
        if (
            not settings.embedding_base_url.strip()
            or not settings.embedding_api_key_value()
            or not settings.embedding_model.strip()
            or not valid_embedding_timeout(settings.embedding_timeout_seconds)
        ):
            raise EmbeddingConfigurationError
        provider = OpenAICompatibleEmbeddingProvider(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key_value(),
            model=settings.embedding_model,
            timeout_seconds=settings.embedding_timeout_seconds,
            dimensions=settings.embedding_dimensions,
            client=client,
        )
    try:
        probe = await provider.embed(["embedding dimension probe"])
        if len(probe) != 1 or len(probe[0]) != EMBEDDING_DIMENSIONS:
            raise EmbeddingConfigurationError
    except Exception:
        if isinstance(provider, OpenAICompatibleEmbeddingProvider):
            await provider.close()
        raise
    return provider


def _usable_cache_path(path: Path) -> bool:
    return bool(str(path).strip())
