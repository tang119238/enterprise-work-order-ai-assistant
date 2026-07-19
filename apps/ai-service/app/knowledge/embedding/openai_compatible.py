import math
from collections.abc import Sequence
from typing import Any

import httpx

from app.knowledge.embedding.base import (
    EMBEDDING_DIMENSIONS,
    EmbeddingConfigurationError,
    EmbeddingProviderAuthenticationError,
    EmbeddingProviderBadResponseError,
    EmbeddingProviderRateLimitError,
    EmbeddingProviderTimeoutError,
    EmbeddingProviderUnavailableError,
    normalize_embeddings,
    require_fixed_dimensions,
)

MAX_EMBEDDING_TIMEOUT_SECONDS = 120.0


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        dimensions: int = EMBEDDING_DIMENSIONS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        require_fixed_dimensions(dimensions)
        if (
            not base_url.strip()
            or not api_key.strip()
            or not model.strip()
            or not valid_embedding_timeout(timeout_seconds)
        ):
            raise EmbeddingConfigurationError
        try:
            parsed_base_url = httpx.URL(base_url)
        except (httpx.InvalidURL, ValueError):
            raise EmbeddingConfigurationError from None
        if (
            parsed_base_url.scheme not in {"http", "https"}
            or not parsed_base_url.host
            or parsed_base_url.userinfo
            or parsed_base_url.query
            or parsed_base_url.fragment
        ):
            raise EmbeddingConfigurationError
        self._endpoint = parsed_base_url.copy_with(
            raw_path=parsed_base_url.raw_path.rstrip(b"/") + b"/embeddings"
        )
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._dimensions = dimensions
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds))
        self._owns_client = client is None
        self._loaded = False

    @property
    def model_key(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def closed(self) -> bool:
        return self._client.is_closed

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._post(list(texts))
        self._raise_for_status(response.status_code)
        raw_vectors = self._parse_response(response, expected_count=len(texts))
        vectors = normalize_embeddings(
            raw_vectors,
            expected_count=len(texts),
            dimensions=self._dimensions,
        )
        self._loaded = True
        return vectors

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _post(self, texts: list[str]) -> httpx.Response:
        try:
            return await self._client.post(
                self._endpoint,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "input": texts,
                    "dimensions": self._dimensions,
                },
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException:
            raise EmbeddingProviderTimeoutError from None
        except httpx.RequestError:
            raise EmbeddingProviderUnavailableError from None

    @staticmethod
    def _raise_for_status(status_code: int) -> None:
        if status_code in {401, 403}:
            raise EmbeddingProviderAuthenticationError
        if status_code == 429:
            raise EmbeddingProviderRateLimitError
        if status_code >= 500:
            raise EmbeddingProviderUnavailableError
        if status_code >= 400:
            raise EmbeddingProviderBadResponseError

    @staticmethod
    def _parse_response(
        response: httpx.Response,
        *,
        expected_count: int,
    ) -> list[Sequence[object]]:
        try:
            body: Any = response.json()
            if not isinstance(body, dict):
                raise EmbeddingProviderBadResponseError
            data = body.get("data")
            if not isinstance(data, list) or len(data) != expected_count:
                raise EmbeddingProviderBadResponseError
            indexed: dict[int, Sequence[object]] = {}
            for item in data:
                if not isinstance(item, dict):
                    raise EmbeddingProviderBadResponseError
                index = item.get("index")
                embedding = item.get("embedding")
                if (
                    isinstance(index, bool)
                    or not isinstance(index, int)
                    or index < 0
                    or index >= expected_count
                    or index in indexed
                    or not isinstance(embedding, Sequence)
                    or isinstance(embedding, str | bytes)
                ):
                    raise EmbeddingProviderBadResponseError
                indexed[index] = embedding
            if set(indexed) != set(range(expected_count)):
                raise EmbeddingProviderBadResponseError
            return [indexed[index] for index in range(expected_count)]
        except EmbeddingProviderBadResponseError:
            raise
        except (TypeError, ValueError):
            raise EmbeddingProviderBadResponseError from None


def valid_embedding_timeout(timeout_seconds: float) -> bool:
    return (
        not isinstance(timeout_seconds, bool)
        and math.isfinite(timeout_seconds)
        and 0 < timeout_seconds <= MAX_EMBEDDING_TIMEOUT_SECONDS
    )
