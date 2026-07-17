from collections.abc import Sequence

import jieba
from rank_bm25 import BM25Plus

from app.knowledge.models import PolicyChunk, SearchHit


def tokenize(text: str) -> list[str]:
    return [token.strip().lower() for token in jieba.lcut(text) if token.strip()]


class BM25PolicyIndex:
    def __init__(self, chunks: Sequence[PolicyChunk]) -> None:
        self._chunks = list(chunks)
        corpus = [tokenize(f"{chunk.title} {chunk.section} {chunk.text}") for chunk in chunks]
        self._index = BM25Plus(corpus) if corpus else None

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        if self._index is None or not query.strip() or limit <= 0:
            return []
        scores = self._index.get_scores(tokenize(query))
        ranked = sorted(
            (
                (float(score), chunk)
                for score, chunk in zip(scores, self._chunks, strict=True)
                if float(score) > 0
            ),
            key=lambda item: (-item[0], item[1].chunk_id),
        )
        return [SearchHit(**chunk.model_dump(), score=score) for score, chunk in ranked[:limit]]
