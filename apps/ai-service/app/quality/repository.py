from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Database
from app.quality.models import (
    ClaimedQualityEvent,
    ClaimedQualityJob,
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

    async def recover_expired(
        self,
        tenant_id: UUID,
        *,
        now: datetime,
        lease_expired_before: datetime,
    ) -> int:
        async with self._database.session(tenant_id) as session:
            recovered = await session.execute(
                text(
                    """
                    UPDATE quality_job
                    SET status = CASE
                            WHEN retry_count >= max_retry_count THEN 'FAILED'
                            ELSE 'RETRY_WAIT'
                        END,
                        next_retry_at = CASE
                            WHEN retry_count >= max_retry_count THEN NULL
                            ELSE :now
                        END,
                        finished_at = CASE
                            WHEN retry_count >= max_retry_count THEN :now
                            ELSE NULL
                        END,
                        started_at = NULL,
                        last_error_code = 'QUALITY_WORKER_LEASE_EXPIRED',
                        last_error_message = 'Quality worker lease expired',
                        updated_at = :now
                    WHERE tenant_id = :tenant_id
                      AND status = 'RUNNING'
                      AND started_at <= :lease_expired_before
                    RETURNING id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "now": now,
                    "lease_expired_before": lease_expired_before,
                },
            )
            return len(recovered.mappings().all())

    async def claim_quality_jobs(
        self,
        tenant_id: UUID,
        limit: int,
        *,
        now: datetime,
    ) -> list[ClaimedQualityJob]:
        async with self._database.session(tenant_id) as session:
            claimed = await session.execute(
                text(
                    """
                    WITH candidates AS (
                        SELECT id
                        FROM quality_job
                        WHERE tenant_id = :tenant_id
                          AND retry_count < max_retry_count
                          AND (
                              status = 'PENDING'
                              OR (status = 'RETRY_WAIT' AND next_retry_at <= :now)
                          )
                        ORDER BY priority, created_at, id
                        FOR UPDATE SKIP LOCKED
                        LIMIT :limit
                    )
                    UPDATE quality_job AS job
                    SET status = 'RUNNING',
                        retry_count = job.retry_count + 1,
                        next_retry_at = NULL,
                        started_at = :now,
                        finished_at = NULL,
                        last_error_code = NULL,
                        last_error_message = NULL,
                        updated_at = :now
                    FROM candidates
                    WHERE job.tenant_id = :tenant_id
                      AND job.id = candidates.id
                      AND job.retry_count < job.max_retry_count
                      AND (
                          job.status = 'PENDING'
                          OR (job.status = 'RETRY_WAIT' AND job.next_retry_at <= :now)
                      )
                    RETURNING job.id, job.tenant_id, job.work_order_id,
                              job.work_order_version, job.inspection_round,
                              job.retry_count, job.trigger_payload
                    """
                ),
                {"tenant_id": tenant_id, "limit": limit, "now": now},
            )
            return [_claimed_quality_job(row) for row in claimed.mappings().all()]

    async def retry_quality_job(
        self,
        job: ClaimedQualityJob,
        *,
        code: str,
        next_retry_at: datetime,
        now: datetime,
    ) -> bool:
        async with self._database.session(job.tenant_id) as session:
            retried = await session.execute(
                text(
                    """
                    UPDATE quality_job
                    SET status = 'RETRY_WAIT',
                        next_retry_at = :next_retry_at,
                        started_at = NULL,
                        last_error_code = :code,
                        last_error_message = :message,
                        updated_at = :now
                    WHERE tenant_id = :tenant_id
                      AND id = :job_id
                      AND status = 'RUNNING'
                      AND retry_count = :retry_count
                      AND retry_count < max_retry_count
                    RETURNING id
                    """
                ),
                {
                    "tenant_id": job.tenant_id,
                    "job_id": job.id,
                    "retry_count": job.retry_count,
                    "code": code,
                    "message": _safe_error_message(code),
                    "next_retry_at": next_retry_at,
                    "now": now,
                },
            )
            return retried.first() is not None

    async def fail_quality_job(
        self,
        job: ClaimedQualityJob,
        *,
        code: str,
        now: datetime,
    ) -> bool:
        async with self._database.session(job.tenant_id) as session:
            failed = await session.execute(
                text(
                    """
                    UPDATE quality_job
                    SET status = 'FAILED',
                        next_retry_at = NULL,
                        started_at = NULL,
                        finished_at = :now,
                        last_error_code = :code,
                        last_error_message = :message,
                        updated_at = :now
                    WHERE tenant_id = :tenant_id
                      AND id = :job_id
                      AND status = 'RUNNING'
                      AND retry_count = :retry_count
                    RETURNING id
                    """
                ),
                {
                    "tenant_id": job.tenant_id,
                    "job_id": job.id,
                    "retry_count": job.retry_count,
                    "code": code,
                    "message": _safe_error_message(code),
                    "now": now,
                },
            )
            return failed.first() is not None

    async def pending_callbacks(
        self,
        tenant_id: UUID,
        limit: int,
    ) -> list[QualityResultRecord]:
        async with self._database.session(tenant_id) as session:
            pending = await session.execute(
                text(
                    """
                    SELECT quality_job_id
                    FROM quality_result
                    WHERE tenant_id = :tenant_id
                      AND callback_at IS NULL
                    ORDER BY generated_at, id
                    LIMIT :limit
                    """
                ),
                {"tenant_id": tenant_id, "limit": limit},
            )
            results: list[QualityResultRecord] = []
            for row in pending.mappings().all():
                result = await _find_result(session, tenant_id, row["quality_job_id"])
                if result is not None:
                    results.append(result)
            return results

    async def mark_callback_delivered(
        self,
        tenant_id: UUID,
        result_id: UUID,
    ) -> bool:
        async with self._database.session(tenant_id) as session:
            result = await session.execute(
                text(
                    """
                    SELECT mark_quality_result_callback_delivered(
                        :tenant_id, :result_id, CURRENT_TIMESTAMP
                    ) AS marked
                    """
                ),
                {"tenant_id": tenant_id, "result_id": result_id},
            )
            return bool(result.scalar_one())


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


def _claimed_quality_job(row: RowMapping) -> ClaimedQualityJob:
    values = dict(row)
    payload = values.pop("trigger_payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise RuntimeError("quality job trigger payload is invalid")
    return ClaimedQualityJob.model_validate(
        {
            **values,
            "work_order_snapshot": payload.get("work_order_snapshot"),
            "attachments_summary": payload.get("attachments_summary", ()),
        }
    )


def _safe_error_message(code: str) -> str:
    messages = {
        "PROVIDER_TIMEOUT": "Model provider request timed out",
        "PROVIDER_RATE_LIMITED": "Model provider rate limited the request",
        "PROVIDER_UNAVAILABLE": "Model provider is unavailable",
        "PROVIDER_AUTH_FAILED": "Model provider authentication failed",
        "PROVIDER_BAD_RESPONSE": "Model provider returned an invalid response",
        "QUALITY_OUTPUT_INVALID": "Model output failed the quality schema",
        "QUALITY_INPUT_INVALID": "Quality job input is invalid",
    }
    return messages.get(code, "Quality inspection failed")
