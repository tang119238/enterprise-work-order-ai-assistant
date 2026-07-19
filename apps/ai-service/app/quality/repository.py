from __future__ import annotations

import json
from uuid import UUID, uuid5

from sqlalchemy import text

from app.db import Database
from app.quality.models import ClaimedQualityEvent, QualityJob

_QUALITY_JOB_NAMESPACE = UUID("7b7c22c6-7930-5c01-89ed-7c347a1ddff5")


class PostgresQualityRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_from_event(self, event: ClaimedQualityEvent) -> QualityJob:
        business_key = quality_business_key(event)
        job_id = uuid5(_QUALITY_JOB_NAMESPACE, business_key)
        trigger_payload = json.dumps(
            {
                "event_id": str(event.event_id),
                "attempt": event.attempt,
                "occurred_at": event.occurred_at.isoformat(),
                "work_order_snapshot": event.work_order_snapshot,
                "attachments_summary": list(event.attachments_summary),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        parameters: dict[str, object] = {
            "id": job_id,
            "tenant_id": event.tenant_id,
            "work_order_id": event.work_order_id,
            "work_order_version": event.work_order_version,
            "inspection_round": event.inspection_round,
            "business_key": business_key,
            "trigger_payload": trigger_payload,
        }
        async with self._database.session(event.tenant_id) as session:
            await session.execute(
                text(
                    """
                    INSERT INTO quality_job (
                        id, tenant_id, work_order_id, work_order_version,
                        inspection_round, business_key, trigger_source,
                        trigger_payload, status
                    ) VALUES (
                        :id, :tenant_id, :work_order_id, :work_order_version,
                        :inspection_round, :business_key, 'WORK_ORDER_COMPLETED',
                        CAST(:trigger_payload AS JSONB), 'PENDING'
                    )
                    ON CONFLICT (tenant_id, work_order_id, work_order_version, inspection_round)
                    DO NOTHING
                    """
                ),
                parameters,
            )
            result = await session.execute(
                text(
                    """
                    SELECT id, tenant_id, work_order_id, work_order_version,
                           inspection_round, business_key, status
                    FROM quality_job
                    WHERE tenant_id = :tenant_id
                      AND work_order_id = :work_order_id
                      AND work_order_version = :work_order_version
                      AND inspection_round = :inspection_round
                    """
                ),
                parameters,
            )
            row = result.mappings().first()
            if row is None:
                raise RuntimeError("quality job insert could not be reloaded")
            return QualityJob.model_validate(row)


def quality_business_key(event: ClaimedQualityEvent) -> str:
    return (
        f"{event.tenant_id}:{event.work_order_id}:"
        f"{event.work_order_version}:{event.inspection_round}"
    )
