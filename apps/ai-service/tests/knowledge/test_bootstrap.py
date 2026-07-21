from pathlib import Path
from uuid import UUID

import pytest

from app.knowledge.bootstrap import ingest_policy_directory
from app.knowledge.models import IngestResult


class FakeIngestor:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str, str, str, str]] = []

    async def ingest(
        self,
        tenant_id: UUID,
        document_key: str,
        title: str,
        markdown: str,
        source_uri: str,
    ) -> IngestResult:
        self.calls.append((tenant_id, document_key, title, markdown, source_uri))
        return IngestResult(
            document_id=UUID("00000000-0000-0000-0000-000000000001"),
            version=1,
            chunk_count=1,
            skipped=False,
        )


TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = UUID("22222222-2222-2222-2222-222222222222")


@pytest.mark.asyncio
async def test_bootstrap_ingests_every_policy_for_each_configured_tenant() -> None:
    ingestor = FakeIngestor()

    count = await ingest_policy_directory(
        ingestor,
        tenant_ids=(TENANT_A, TENANT_B),
        directory=Path("knowledge/policies"),
    )

    assert count == 6
    assert {(call[0], call[1]) for call in ingestor.calls} == {
        (tenant_id, document_id)
        for tenant_id in (TENANT_A, TENANT_B)
        for document_id in ("rework-policy", "sla-policy", "work-order-lifecycle")
    }
    assert all(call[4].startswith("policy://synthetic/") for call in ingestor.calls)
    assert all("C:\\" not in call[4] for call in ingestor.calls)


@pytest.mark.asyncio
async def test_bootstrap_fails_when_policy_directory_is_missing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="policy directory"):
        await ingest_policy_directory(
            FakeIngestor(),
            tenant_ids=(TENANT_A,),
            directory=tmp_path / "missing",
        )
