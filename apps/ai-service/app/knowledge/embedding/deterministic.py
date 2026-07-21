import hashlib
import struct
from collections.abc import Sequence

from app.knowledge.embedding.base import (
    EMBEDDING_DIMENSIONS,
    normalize_embeddings,
    require_fixed_dimensions,
)


class DeterministicEmbeddingProvider:
    """A reproducible test provider; it does not represent semantic similarity."""

    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        require_fixed_dimensions(dimensions)
        self._dimensions = dimensions

    @property
    def model_key(self) -> str:
        return "deterministic-shake256-v1"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def loaded(self) -> bool:
        return True

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = [self._vector(text) for text in texts]
        return normalize_embeddings(
            vectors,
            expected_count=len(texts),
            dimensions=self._dimensions,
        )

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.shake_256(text.encode("utf-8")).digest(self._dimensions * 4)
        unsigned = struct.unpack(f">{self._dimensions}I", digest)
        midpoint = float(2**31)
        return [(value - midpoint) / midpoint for value in unsigned]
