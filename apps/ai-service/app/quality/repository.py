from __future__ import annotations

import json
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Database
from app.quality.models import (
    ClaimedQualityEvent,
    QualityJob,
    QualityResultRecord,
)

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

    async def find_result(
        self,
        tenant_id: UUID,
        job_id: UUID,
    ) -> QualityResultRecord | None:
        async with self._database.session(tenant_id) as session:
            return await _find_result(session, tenant_id, job_id)

    async def save_result(self, result: QualityResultRecord) -> QualityResultRecord:
        parameters = {
            "tenant_id": result.tenant_id,
            "job_id": result.quality_job_id,
        }
        async with self._database.session(result.tenant_id) as session:
            locked = await session.execute(
                text(
                    """
                    SELECT id
                    FROM quality_job
                    WHERE tenant_id = :tenant_id AND id = :job_id
                    FOR UPDATE
                    """
                ),
                parameters,
            )
            if locked.first() is None:
                raise RuntimeError("quality job does not exist for result persistence")

            existing = await _find_result(
                session,
                result.tenant_id,
                result.quality_job_id,
            )
            if existing is not None:
                return existing

            if result.model_call is not None:
                audit = result.model_call
                await session.execute(
                    text(
                        """
                        INSERT INTO model_call_audit (
                            id, tenant_id, quality_job_id, provider, model_name,
                            prompt_version, request_id, latency_ms, input_tokens,
                            output_tokens, estimated_cost, input_summary,
                            response_summary, raw_response_truncated,
                            error_code, error_message
                        ) VALUES (
                            :id, :tenant_id, :quality_job_id, :provider, :model_name,
                            :prompt_version, :request_id, :latency_ms, :input_tokens,
                            :output_tokens, :estimated_cost, CAST(:input_summary AS JSONB),
                            CAST(:response_summary AS JSONB), :raw_response_truncated,
                            :error_code, :error_message
                        )
                        """
                    ),
                    {
                        "id": audit.id,
                        "tenant_id": result.tenant_id,
                        "quality_job_id": result.quality_job_id,
                        "provider": audit.provider,
                        "model_name": audit.model_name,
                        "prompt_version": audit.prompt_version,
                        "request_id": audit.request_id,
                        "latency_ms": audit.latency_ms,
                        "input_tokens": audit.input_tokens,
                        "output_tokens": audit.output_tokens,
                        "estimated_cost": audit.estimated_cost,
                        "input_summary": _json(audit.input_summary),
                        "response_summary": _json(audit.response_summary),
                        "raw_response_truncated": audit.raw_response_truncated,
                        "error_code": audit.error_code,
                        "error_message": audit.error_message,
                    },
                )

            await session.execute(
                text(
                    """
                    INSERT INTO quality_result (
                        id, tenant_id, quality_job_id, work_order_id,
                        work_order_version, inspection_round, model_call_id,
                        verdict, confidence, work_order_snapshot, policy_versions,
                        attachment_summary
                    ) VALUES (
                        :id, :tenant_id, :quality_job_id, :work_order_id,
                        :work_order_version, :inspection_round, :model_call_id,
                        :verdict, :confidence, CAST(:work_order_snapshot AS JSONB),
                        CAST(:policy_versions AS JSONB), CAST(:attachment_summary AS JSONB)
                    )
                    """
                ),
                {
                    "id": result.id,
                    "tenant_id": result.tenant_id,
                    "quality_job_id": result.quality_job_id,
                    "work_order_id": result.work_order_id,
                    "work_order_version": result.work_order_version,
                    "inspection_round": result.inspection_round,
                    "model_call_id": (
                        result.model_call.id if result.model_call is not None else None
                    ),
                    "verdict": result.verdict,
                    "confidence": result.confidence,
                    "work_order_snapshot": _json(result.work_order_snapshot),
                    "policy_versions": _json(result.policy_versions),
                    "attachment_summary": _json(result.attachment_summary),
                },
            )
            await session.execute(
                text(
                    """
                    INSERT INTO quality_finding (
                        id, tenant_id, quality_result_id, ordinal, rule_code,
                        severity, label, evidence, policy_chunk_id,
                        recommendation, confidence, source
                    ) VALUES (
                        :id, :tenant_id, :quality_result_id, :ordinal, :rule_code,
                        :severity, :label, CAST(:evidence AS JSONB), :policy_chunk_id,
                        :recommendation, :confidence, :source
                    )
                    """
                ),
                [
                    {
                        "id": uuid5(
                            _QUALITY_FINDING_NAMESPACE,
                            f"{result.tenant_id}:{result.id}:{finding.ordinal}",
                        ),
                        "tenant_id": result.tenant_id,
                        "quality_result_id": result.id,
                        "ordinal": finding.ordinal,
                        "rule_code": finding.rule_code,
                        "severity": finding.severity,
                        "label": finding.label,
                        "evidence": _json(finding.evidence),
                        "policy_chunk_id": finding.policy_chunk_id,
                        "recommendation": finding.recommendation,
                        "confidence": finding.confidence,
                        "source": finding.source,
                    }
                    for finding in result.findings
                ],
            )
            await session.execute(
                text(
                    """
                    UPDATE quality_job
                    SET status = :status,
                        result_id = :result_id,
                        finished_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP,
                        last_error_code = NULL,
                        last_error_message = NULL
                    WHERE tenant_id = :tenant_id AND id = :job_id
                    """
                ),
                {
                    **parameters,
                    "result_id": result.id,
                    "status": "SKIPPED" if result.verdict == "SKIP" else "SUCCEEDED",
                },
            )
            return result


def quality_business_key(event: ClaimedQualityEvent) -> str:
    return (
        f"{event.tenant_id}:{event.work_order_id}:"
        f"{event.work_order_version}:{event.inspection_round}"
    )


_QUALITY_FINDING_NAMESPACE = UUID("7b7c22c6-7930-5c01-89ed-7c347a1ddff6")


async def _find_result(
    session: AsyncSession,
    tenant_id: UUID,
    job_id: UUID,
) -> QualityResultRecord | None:
    result = await session.execute(
        text(
            """
            SELECT jsonb_build_object(
                'id', r.id,
                'tenant_id', r.tenant_id,
                'quality_job_id', r.quality_job_id,
                'work_order_id', r.work_order_id,
                'work_order_version', r.work_order_version,
                'inspection_round', r.inspection_round,
                'verdict', r.verdict,
                'confidence', r.confidence,
                'work_order_snapshot', r.work_order_snapshot,
                'policy_versions', r.policy_versions,
                'attachment_summary', r.attachment_summary,
                'findings', COALESCE((
                    SELECT jsonb_agg(jsonb_build_object(
                        'ordinal', f.ordinal,
                        'rule_code', f.rule_code,
                        'severity', f.severity,
                        'label', f.label,
                        'evidence', f.evidence,
                        'policy_chunk_id', f.policy_chunk_id,
                        'recommendation', f.recommendation,
                        'confidence', f.confidence,
                        'source', f.source
                    ) ORDER BY f.ordinal)
                    FROM quality_finding AS f
                    WHERE f.tenant_id = r.tenant_id
                      AND f.quality_result_id = r.id
                ), '[]'::jsonb),
                'model_call', CASE WHEN a.id IS NULL THEN NULL ELSE jsonb_build_object(
                    'id', a.id,
                    'provider', a.provider,
                    'model_name', a.model_name,
                    'prompt_version', a.prompt_version,
                    'request_id', a.request_id,
                    'latency_ms', a.latency_ms,
                    'input_tokens', a.input_tokens,
                    'output_tokens', a.output_tokens,
                    'estimated_cost', a.estimated_cost,
                    'input_summary', a.input_summary,
                    'response_summary', a.response_summary,
                    'raw_response_truncated', a.raw_response_truncated,
                    'error_code', a.error_code,
                    'error_message', a.error_message
                ) END
            ) AS payload
            FROM quality_result AS r
            LEFT JOIN model_call_audit AS a
              ON a.tenant_id = r.tenant_id AND a.id = r.model_call_id
            WHERE r.tenant_id = :tenant_id AND r.quality_job_id = :job_id
            """
        ),
        {"tenant_id": tenant_id, "job_id": job_id},
    )
    row = result.mappings().first()
    if row is None:
        return None
    return QualityResultRecord.model_validate(row["payload"])


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
