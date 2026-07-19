import asyncio
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, Protocol, cast

from app.knowledge.embedding.base import (
    EMBEDDING_DIMENSIONS,
    EmbeddingError,
    EmbeddingProviderUnavailableError,
    normalize_embeddings,
    require_fixed_dimensions,
)

FASTEMBED_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
_PROBE_TEXT = "embedding dimension probe"


class FastEmbedModel(Protocol):
    def embed(self, documents: Iterable[str]) -> Iterable[Sequence[object]]: ...


ModelFactory = Callable[..., FastEmbedModel]


class FastEmbedEmbeddingProvider:
    def __init__(
        self,
        *,
        cache_path: Path,
        dimensions: int = EMBEDDING_DIMENSIONS,
        model_factory: ModelFactory | None = None,
    ) -> None:
        require_fixed_dimensions(dimensions)
        self._cache_path = cache_path
        self._dimensions = dimensions
        self._model_factory = model_factory or _default_model_factory
        self._model: FastEmbedModel | None = None
        self._loaded = False
        self._load_lock = asyncio.Lock()

    @property
    def model_key(self) -> str:
        return FASTEMBED_MODEL_NAME

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def loaded(self) -> bool:
        return self._loaded

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model = await self._ensure_loaded()
        try:
            raw_vectors = await asyncio.to_thread(self._infer, model, list(texts))
        except Exception as error:
            if isinstance(error, EmbeddingError):
                raise
            raise EmbeddingProviderUnavailableError from None
        return normalize_embeddings(
            raw_vectors,
            expected_count=len(texts),
            dimensions=self._dimensions,
        )

    async def _ensure_loaded(self) -> FastEmbedModel:
        if self._model is not None and self._loaded:
            return self._model
        async with self._load_lock:
            if self._model is not None and self._loaded:
                return self._model
            try:
                model = await asyncio.to_thread(self._construct_and_probe)
            except Exception as error:
                if isinstance(error, EmbeddingError):
                    raise
                raise EmbeddingProviderUnavailableError from None
            self._model = model
            self._loaded = True
            return model

    def _construct_and_probe(self) -> FastEmbedModel:
        model = self._model_factory(
            model_name=FASTEMBED_MODEL_NAME,
            cache_dir=str(self._cache_path),
        )
        probe = self._infer(model, [_PROBE_TEXT])
        normalize_embeddings(probe, expected_count=1, dimensions=self._dimensions)
        return model

    @staticmethod
    def _infer(model: FastEmbedModel, texts: list[str]) -> list[Sequence[object]]:
        return list(model.embed(texts))


def _default_model_factory(**kwargs: Any) -> FastEmbedModel:
    # Import lazily so importing application modules never loads or downloads a model.
    from fastembed import TextEmbedding

    return cast(FastEmbedModel, TextEmbedding(**kwargs))
