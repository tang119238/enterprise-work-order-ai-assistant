-- Analytics views for NL2SQL queries
-- security_invoker ensures RLS policies are enforced per-querying-role
-- Views expose only non-sensitive columns suitable for analytics

CREATE VIEW analytics_work_order_v WITH (security_invoker = true) AS
SELECT
    wo.id,
    wo.tenant_id,
    wo.project_id,
    wo.work_order_no,
    wo.title,
    wo.project_name,
    wo.order_type,
    wo.priority,
    wo.status,
    wo.source,
    wo.assignee_name,
    wo.root_work_order_no,
    wo.created_at,
    wo.due_at,
    wo.accepted_at,
    wo.completed_at,
    wo.cancelled_at,
    CASE
        WHEN wo.status IN ('COMPLETED', 'CLOSED') THEN
            EXTRACT(EPOCH FROM (wo.completed_at - wo.created_at)) / 3600
        ELSE NULL
    END AS completion_hours,
    CASE
        WHEN wo.due_at < CURRENT_TIMESTAMP
            AND wo.status NOT IN ('COMPLETED', 'CLOSED', 'CANCELLED')
        THEN true
        ELSE false
    END AS is_overdue
FROM work_order wo;

CREATE VIEW analytics_quality_v WITH (security_invoker = true) AS
SELECT
    qr.id,
    qr.tenant_id,
    wo.project_id,
    qr.work_order_id,
    wo.work_order_no,
    wo.title AS work_order_title,
    wo.project_name,
    qr.verdict,
    qr.confidence,
    qr.inspection_round,
    qr.generated_at,
    mca.provider AS model_provider,
    mca.model_name,
    mca.prompt_version,
    mca.latency_ms AS model_latency_ms,
    qf_count.finding_count,
    qf_count.high_severity_count,
    qf_count.medium_severity_count,
    qf_count.low_severity_count
FROM quality_result qr
JOIN work_order wo ON wo.tenant_id = qr.tenant_id AND wo.id = qr.work_order_id
LEFT JOIN model_call_audit mca ON mca.tenant_id = qr.tenant_id AND mca.id = qr.model_call_id
LEFT JOIN LATERAL (
    SELECT
        qf.quality_result_id,
        COUNT(*) AS finding_count,
        COUNT(*) FILTER (WHERE qf.severity = 'HIGH') AS high_severity_count,
        COUNT(*) FILTER (WHERE qf.severity = 'MEDIUM') AS medium_severity_count,
        COUNT(*) FILTER (WHERE qf.severity = 'LOW') AS low_severity_count
    FROM quality_finding qf
    WHERE qf.tenant_id = qr.tenant_id AND qf.quality_result_id = qr.id
    GROUP BY qf.quality_result_id
) qf_count ON true;

CREATE VIEW analytics_rectification_v WITH (security_invoker = true) AS
SELECT
    rc.id,
    rc.tenant_id,
    wo.project_id,
    rc.original_work_order_id,
    wo_orig.work_order_no AS original_work_order_no,
    wo_orig.title AS original_work_order_title,
    wo_orig.project_name,
    rc.rectification_work_order_id,
    wo_rect.work_order_no AS rectification_work_order_no,
    rc.current_verdict,
    rc.inspection_round,
    rc.status,
    rc.created_at,
    rc.closed_at,
    CASE
        WHEN rc.closed_at IS NOT NULL THEN
            EXTRACT(EPOCH FROM (rc.closed_at - rc.created_at)) / 3600
        ELSE NULL
    END AS resolution_hours
FROM rectification_case rc
JOIN work_order wo_orig
    ON wo_orig.tenant_id = rc.tenant_id AND wo_orig.id = rc.original_work_order_id
JOIN work_order wo
    ON wo.tenant_id = rc.tenant_id AND wo.id = rc.original_work_order_id
LEFT JOIN work_order wo_rect
    ON wo_rect.tenant_id = rc.tenant_id AND wo_rect.id = rc.rectification_work_order_id;
