-- Analytics query audit table and role grants
-- analytics_reader gets SELECT on views only, no base table access

CREATE TABLE analytics_query_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    user_id UUID NOT NULL,
    question_summary VARCHAR(500) NOT NULL,
    catalog_version VARCHAR(64) NOT NULL,
    model_provider VARCHAR(100),
    model_name VARCHAR(200),
    generated_sql TEXT NOT NULL,
    validation_stage VARCHAR(64) NOT NULL,
    rejection_reason VARCHAR(500),
    executed BOOLEAN NOT NULL DEFAULT false,
    execution_ms INTEGER,
    row_count INTEGER,
    truncated BOOLEAN NOT NULL DEFAULT false,
    request_id VARCHAR(128),
    trace_id VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_analytics_audit_tenant FOREIGN KEY (tenant_id) REFERENCES tenant(id),
    CONSTRAINT fk_analytics_audit_user FOREIGN KEY (user_id) REFERENCES user_identity(id),
    CONSTRAINT ck_analytics_audit_stage CHECK (validation_stage IN (
        'PYTHON_PARSE', 'PYTHON_POLICY', 'JAVA_PARSE', 'JAVA_POLICY',
        'COST_CHECK', 'EXECUTED', 'REJECTED'
    ))
);

CREATE INDEX idx_analytics_audit_tenant_created
    ON analytics_query_audit(tenant_id, created_at DESC);
CREATE INDEX idx_analytics_audit_tenant_user
    ON analytics_query_audit(tenant_id, user_id, created_at DESC);

-- Enable RLS on audit table
ALTER TABLE analytics_query_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics_query_audit FORCE ROW LEVEL SECURITY;
CREATE POLICY analytics_audit_tenant_policy ON analytics_query_audit
USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

-- Grants: analytics_reader can SELECT views and INSERT audit records
GRANT SELECT ON analytics_work_order_v TO analytics_reader;
GRANT SELECT ON analytics_quality_v TO analytics_reader;
GRANT SELECT ON analytics_rectification_v TO analytics_reader;
GRANT INSERT ON analytics_query_audit TO analytics_reader;

-- Deny base table access
REVOKE ALL PRIVILEGES ON TABLE work_order FROM analytics_reader;
REVOKE ALL PRIVILEGES ON TABLE quality_result FROM analytics_reader;
REVOKE ALL PRIVILEGES ON TABLE quality_finding FROM analytics_reader;
REVOKE ALL PRIVILEGES ON TABLE rectification_case FROM analytics_reader;
REVOKE ALL PRIVILEGES ON TABLE quality_review_event FROM analytics_reader;
REVOKE ALL PRIVILEGES ON TABLE action_proposal FROM analytics_reader;
REVOKE ALL PRIVILEGES ON TABLE tenant FROM analytics_reader;
REVOKE ALL PRIVILEGES ON TABLE user_identity FROM analytics_reader;
