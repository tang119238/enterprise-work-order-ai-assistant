import math
from collections.abc import Sequence
from numbers import Real
from typing import Protocol, runtime_checkable

EMBEDDING_DIMENSIONS = 512


class EmbeddingError(RuntimeError):
    code = "EMBEDDING_ERROR"
    retryable = False

    def __init__(self, message: str = "Embedding operation failed") -> None:
        super().__init__(message)


class EmbeddingCapabilityError(EmbeddingError):
    code = "EMBEDDING_CAPABILITY_DISABLED"

    def __init__(self) -> None:
        super().__init__("Embedding capability is disabled")


class EmbeddingConfigurationError(EmbeddingError):
    code = "EMBEDDING_CONFIGURATION_INVALID"

    def __init__(self) -> None:
        super().__init__("Embedding provider configuration is invalid")


class EmbeddingProviderTimeoutError(EmbeddingError):
    code = "EMBEDDING_PROVIDER_TIMEOUT"
    retryable = True

    def __init__(self) -> None:
        super().__init__("Embedding provider request timed out")


class EmbeddingProviderUnavailableError(EmbeddingError):
    code = "EMBEDDING_PROVIDER_UNAVAILABLE"
    retryable = True

    def __init__(self) -> None:
        super().__init__("Embedding provider is unavailable")


class EmbeddingProviderAuthenticationError(EmbeddingError):
    code = "EMBEDDING_PROVIDER_AUTH_FAILED"

    def __init__(self) -> None:
        super().__init__("Embedding provider authentication failed")


class EmbeddingProviderRateLimitError(EmbeddingError):
    code = "EMBEDDING_PROVIDER_RATE_LIMITED"
    retryable = True

    def __init__(self) -> None:
        super().__init__("Embedding provider rate limited the request")


class EmbeddingProviderBadResponseError(EmbeddingError):
    code = "EMBEDDING_PROVIDER_BAD_RESPONSE"

    def __init__(self) -> None:
        super().__init__("Embedding provider returned an invalid response")


class EmbeddingValidationError(EmbeddingError):
    code = "EMBEDDING_VECTOR_INVALID"

    def __init__(self) -> None:
        super().__init__("Embedding provider returned invalid vectors")


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def model_key(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    @property
    def loaded(self) -> bool: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def require_fixed_dimensions(dimensions: int) -> None:
    if dimensions != EMBEDDING_DIMENSIONS:
        raise EmbeddingConfigurationError


def normalize_embeddings(
    vectors: Sequence[Sequence[object]],
    *,
    expected_count: int,
    dimensions: int = EMBEDDING_DIMENSIONS,
) -> list[list[float]]:
    """Validate and L2-normalize provider output without retaining input data."""
    require_fixed_dimensions(dimensions)
    try:
        if len(vectors) != expected_count:
            raise EmbeddingValidationError
        normalized: list[list[float]] = []
        for vector in vectors:
            if len(vector) != dimensions:
                raise EmbeddingValidationError
            values: list[float] = []
            for value in vector:
                if isinstance(value, bool) or not isinstance(value, Real):
                    raise EmbeddingValidationError
                numeric = float(value)
                if not math.isfinite(numeric):
                    raise EmbeddingValidationError
                values.append(numeric)
            norm = math.sqrt(math.fsum(value * value for value in values))
            if not math.isfinite(norm) or norm == 0.0:
                raise EmbeddingValidationError
            normalized.append([value / norm for value in values])
        return normalized
    except EmbeddingError:
        raise
    except (OverflowError, TypeError, ValueError):
        raise EmbeddingValidationError from None
