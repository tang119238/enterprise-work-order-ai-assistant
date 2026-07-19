from collections.abc import Sequence
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.knowledge.loader import load_policy
from app.knowledge.models import IngestResult


class PolicyIngestor(Protocol):
    async def ingest(
        self,
        tenant_id: UUID,
        document_key: str,
        title: str,
        markdown: str,
        source_uri: str,
    ) -> IngestResult: ...


async def ingest_policy_directory(
    ingestor: PolicyIngestor,
    *,
    tenant_ids: Sequence[UUID],
    directory: Path,
) -> int:
    if not directory.is_dir():
        raise ValueError("policy directory is not available")
    paths = sorted(directory.glob("*.md"))
    if not paths:
        raise ValueError("policy directory contains no Markdown documents")
    documents: list[tuple[str, str, str, str]] = []
    for path in paths:
        markdown = path.read_text(encoding="utf-8")
        chunks = load_policy(path)
        if not chunks:
            raise ValueError(f"policy {path.name} contains no retrievable sections")
        documents.append(
            (
                chunks[0].document_id,
                chunks[0].title,
                markdown,
                f"policy://synthetic/{path.name}",
            )
        )
    if len({document[0] for document in documents}) != len(documents):
        raise ValueError("policy document ids must be unique")
    count = 0
    for tenant_id in tenant_ids:
        if not isinstance(tenant_id, UUID):
            raise TypeError("policy tenant ids must be UUIDs")
        for document_key, title, markdown, source_uri in documents:
            await ingestor.ingest(
                tenant_id,
                document_key,
                title,
                markdown,
                source_uri,
            )
            count += 1
    return count
