CREATE TABLE rectification_case (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    original_work_order_id UUID NOT NULL,
    current_quality_result_id UUID NOT NULL,
    rectification_work_order_id UUID,
    inspection_round INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PROPOSED',
    created_by UUID,
    updated_by UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMPTZ,
    CONSTRAINT pk_rectification_case PRIMARY KEY (tenant_id, id),
    CONSTRAINT fk_rectification_case_tenant FOREIGN KEY (tenant_id) REFERENCES tenant(id),
    CONSTRAINT fk_rectification_case_original_work_order
        FOREIGN KEY (tenant_id, original_work_order_id) REFERENCES work_order(tenant_id, id),
    CONSTRAINT fk_rectification_case_rectification_work_order
        FOREIGN KEY (tenant_id, rectification_work_order_id) REFERENCES work_order(tenant_id, id),
    CONSTRAINT fk_rectification_case_created_by FOREIGN KEY (created_by) REFERENCES user_identity(id),
    CONSTRAINT fk_rectification_case_updated_by FOREIGN KEY (updated_by) REFERENCES user_identity(id),
    CONSTRAINT uq_rectification_case_business_key
        UNIQUE (tenant_id, original_work_order_id, inspection_round),
    CONSTRAINT ck_rectification_case_round CHECK (inspection_round > 0),
    CONSTRAINT ck_rectification_case_status CHECK (
        status IN ('PROPOSED', 'RECTIFYING', 'RECHECKING', 'CLOSED')
    ),
    CONSTRAINT ck_rectification_case_closed_at CHECK (
        (status = 'CLOSED' AND closed_at IS NOT NULL)
        OR (status <> 'CLOSED' AND closed_at IS NULL)
    )
);

CREATE INDEX idx_rectification_case_tenant_status
    ON rectification_case(tenant_id, status, updated_at);
CREATE INDEX idx_rectification_case_tenant_original_work_order
    ON rectification_case(tenant_id, original_work_order_id, inspection_round);
CREATE INDEX idx_rectification_case_tenant_rectification_work_order
    ON rectification_case(tenant_id, rectification_work_order_id);

CREATE TABLE quality_review_event (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    rectification_case_id UUID NOT NULL,
    quality_result_id UUID NOT NULL,
    decision VARCHAR(32) NOT NULL,
    previous_verdict VARCHAR(32) NOT NULL,
    reviewed_verdict VARCHAR(32) NOT NULL,
    reason TEXT NOT NULL,
    review_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    actor_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_quality_review_event PRIMARY KEY (tenant_id, id),
    CONSTRAINT fk_quality_review_event_case
        FOREIGN KEY (tenant_id, rectification_case_id) REFERENCES rectification_case(tenant_id, id),
    CONSTRAINT fk_quality_review_event_actor FOREIGN KEY (actor_id) REFERENCES user_identity(id),
    CONSTRAINT ck_quality_review_event_decision CHECK (
        decision IN ('ACCEPT', 'OVERRIDE', 'REQUEST_REWORK', 'CLOSE')
    ),
    CONSTRAINT ck_quality_review_event_previous_verdict CHECK (
        previous_verdict IN ('PASS', 'FAIL', 'UNCERTAIN', 'SKIP')
    ),
    CONSTRAINT ck_quality_review_event_reviewed_verdict CHECK (
        reviewed_verdict IN ('PASS', 'FAIL', 'UNCERTAIN', 'SKIP')
    ),
    CONSTRAINT ck_quality_review_event_reason CHECK (btrim(reason) <> '')
);

CREATE INDEX idx_quality_review_event_tenant_case_created_at
    ON quality_review_event(tenant_id, rectification_case_id, created_at);
CREATE INDEX idx_quality_review_event_tenant_result
    ON quality_review_event(tenant_id, quality_result_id);

ALTER TABLE rectification_case ENABLE ROW LEVEL SECURITY;
ALTER TABLE rectification_case FORCE ROW LEVEL SECURITY;
CREATE POLICY rectification_case_tenant_policy ON rectification_case
USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

ALTER TABLE quality_review_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE quality_review_event FORCE ROW LEVEL SECURITY;
CREATE POLICY quality_review_event_tenant_policy ON quality_review_event
USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

REVOKE ALL PRIVILEGES ON TABLE rectification_case FROM PUBLIC, ai_app, analytics_reader, work_order_app;
REVOKE ALL PRIVILEGES ON TABLE quality_review_event FROM PUBLIC, ai_app, analytics_reader, work_order_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE rectification_case TO work_order_app;
GRANT SELECT, INSERT ON TABLE quality_review_event TO work_order_app;
