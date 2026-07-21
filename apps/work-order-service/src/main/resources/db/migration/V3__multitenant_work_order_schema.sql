ALTER TABLE work_order RENAME TO work_order_legacy;
ALTER TABLE work_order_legacy RENAME CONSTRAINT work_order_pkey TO work_order_legacy_pkey;

CREATE TABLE tenant (
    id UUID PRIMARY KEY,
    tenant_key VARCHAR(64) NOT NULL,
    name VARCHAR(200) NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_tenant_tenant_key UNIQUE (tenant_key),
    CONSTRAINT ck_tenant_status CHECK (status IN ('ACTIVE', 'INACTIVE'))
);

CREATE TABLE user_identity (
    id UUID PRIMARY KEY,
    issuer VARCHAR(300) NOT NULL,
    subject VARCHAR(300) NOT NULL,
    display_name VARCHAR(200) NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_user_identity_issuer_subject UNIQUE (issuer, subject),
    CONSTRAINT ck_user_identity_status CHECK (status IN ('ACTIVE', 'INACTIVE'))
);

CREATE TABLE tenant_membership (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    user_identity_id UUID NOT NULL,
    role VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_tenant_membership_tenant FOREIGN KEY (tenant_id) REFERENCES tenant(id),
    CONSTRAINT fk_tenant_membership_user_identity FOREIGN KEY (user_identity_id) REFERENCES user_identity(id),
    CONSTRAINT uq_tenant_membership_tenant_user_role UNIQUE (tenant_id, user_identity_id, role),
    CONSTRAINT ck_tenant_membership_status CHECK (status IN ('ACTIVE', 'INACTIVE'))
);

CREATE INDEX idx_tenant_membership_tenant_status
    ON tenant_membership(tenant_id, status);

CREATE TABLE project (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    project_key VARCHAR(64) NOT NULL,
    name VARCHAR(100) NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_project_tenant FOREIGN KEY (tenant_id) REFERENCES tenant(id),
    CONSTRAINT uq_project_tenant_id UNIQUE (tenant_id, id),
    CONSTRAINT uq_project_tenant_project_key UNIQUE (tenant_id, project_key),
    CONSTRAINT ck_project_status CHECK (status IN ('ACTIVE', 'INACTIVE'))
);

CREATE INDEX idx_project_tenant_status ON project(tenant_id, status);

CREATE TABLE project_scope (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    user_identity_id UUID NOT NULL,
    project_id UUID NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_project_scope_tenant FOREIGN KEY (tenant_id) REFERENCES tenant(id),
    CONSTRAINT fk_project_scope_user_identity FOREIGN KEY (user_identity_id) REFERENCES user_identity(id),
    CONSTRAINT fk_project_scope_project
        FOREIGN KEY (tenant_id, project_id) REFERENCES project(tenant_id, id),
    CONSTRAINT uq_project_scope_tenant_user_project
        UNIQUE (tenant_id, user_identity_id, project_id),
    CONSTRAINT ck_project_scope_status CHECK (status IN ('ACTIVE', 'INACTIVE'))
);

CREATE INDEX idx_project_scope_tenant_user_status
    ON project_scope(tenant_id, user_identity_id, status);

CREATE TABLE work_order (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    work_order_no VARCHAR(32) NOT NULL,
    title VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    project_id UUID NOT NULL,
    project_name VARCHAR(100) NOT NULL,
    space_path VARCHAR(300) NOT NULL,
    order_type VARCHAR(32) NOT NULL,
    priority VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    assignee_id UUID,
    assignee_name VARCHAR(64),
    source VARCHAR(32) NOT NULL,
    root_work_order_id UUID,
    root_work_order_no VARCHAR(32),
    rework_reason VARCHAR(300),
    version BIGINT NOT NULL DEFAULT 0,
    accepted_at TIMESTAMP,
    created_by UUID,
    updated_by UUID,
    created_at TIMESTAMP NOT NULL,
    due_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    cancelled_at TIMESTAMP,
    cancel_reason VARCHAR(300),
    CONSTRAINT fk_work_order_tenant FOREIGN KEY (tenant_id) REFERENCES tenant(id),
    CONSTRAINT fk_work_order_project
        FOREIGN KEY (tenant_id, project_id) REFERENCES project(tenant_id, id),
    CONSTRAINT fk_work_order_assignee FOREIGN KEY (assignee_id) REFERENCES user_identity(id),
    CONSTRAINT fk_work_order_created_by FOREIGN KEY (created_by) REFERENCES user_identity(id),
    CONSTRAINT fk_work_order_updated_by FOREIGN KEY (updated_by) REFERENCES user_identity(id),
    CONSTRAINT uq_work_order_tenant_id UNIQUE (tenant_id, id),
    CONSTRAINT uq_work_order_tenant_work_order_no UNIQUE (tenant_id, work_order_no),
    CONSTRAINT fk_work_order_multitenant_root
        FOREIGN KEY (tenant_id, root_work_order_id) REFERENCES work_order(tenant_id, id),
    CONSTRAINT ck_work_order_status CHECK (status IN (
        'PENDING_DISPATCH', 'PENDING_ACCEPTANCE', 'PROCESSING', 'COMPLETED', 'CLOSED', 'CANCELLED'
    ))
);

CREATE INDEX idx_work_order_tenant_status ON work_order(tenant_id, status);
CREATE INDEX idx_work_order_tenant_priority ON work_order(tenant_id, priority);
CREATE INDEX idx_work_order_tenant_project ON work_order(tenant_id, project_id);
CREATE INDEX idx_work_order_tenant_assignee ON work_order(tenant_id, assignee_id);
CREATE INDEX idx_work_order_tenant_created_at ON work_order(tenant_id, created_at DESC);
CREATE INDEX idx_work_order_tenant_root ON work_order(tenant_id, root_work_order_id);

CREATE TABLE action_proposal (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    action_type VARCHAR(64) NOT NULL,
    target_id UUID,
    command_payload JSONB NOT NULL,
    before_snapshot JSONB NOT NULL,
    after_snapshot JSONB NOT NULL,
    risk_level VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    requested_by UUID NOT NULL,
    confirmed_by UUID,
    expected_version BIGINT,
    expires_at TIMESTAMP NOT NULL,
    execution_result JSONB,
    error_code VARCHAR(64),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_action_proposal_tenant FOREIGN KEY (tenant_id) REFERENCES tenant(id),
    CONSTRAINT fk_action_proposal_target
        FOREIGN KEY (tenant_id, target_id) REFERENCES work_order(tenant_id, id),
    CONSTRAINT fk_action_proposal_requested_by FOREIGN KEY (requested_by) REFERENCES user_identity(id),
    CONSTRAINT fk_action_proposal_confirmed_by FOREIGN KEY (confirmed_by) REFERENCES user_identity(id),
    CONSTRAINT ck_action_proposal_risk_level CHECK (risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    CONSTRAINT ck_action_proposal_status CHECK (status IN (
        'PENDING_CONFIRMATION', 'CONFIRMED', 'REJECTED', 'EXPIRED', 'EXECUTING', 'EXECUTED', 'FAILED'
    ))
);

CREATE INDEX idx_action_proposal_tenant_status ON action_proposal(tenant_id, status);
CREATE INDEX idx_action_proposal_tenant_target ON action_proposal(tenant_id, target_id);

CREATE TABLE work_order_assignment (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    work_order_id UUID NOT NULL,
    assignee_id UUID NOT NULL,
    assigned_at TIMESTAMP NOT NULL,
    unassigned_at TIMESTAMP,
    reason VARCHAR(300) NOT NULL,
    created_by UUID,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_work_order_assignment_work_order
        FOREIGN KEY (tenant_id, work_order_id) REFERENCES work_order(tenant_id, id),
    CONSTRAINT fk_work_order_assignment_assignee FOREIGN KEY (assignee_id) REFERENCES user_identity(id),
    CONSTRAINT fk_work_order_assignment_created_by FOREIGN KEY (created_by) REFERENCES user_identity(id),
    CONSTRAINT ck_work_order_assignment_interval CHECK (unassigned_at IS NULL OR unassigned_at >= assigned_at)
);

CREATE INDEX idx_work_order_assignment_tenant_work_order
    ON work_order_assignment(tenant_id, work_order_id, assigned_at DESC);

CREATE TABLE work_order_event (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    work_order_id UUID NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    command_type VARCHAR(64) NOT NULL,
    before_snapshot JSONB NOT NULL,
    after_snapshot JSONB NOT NULL,
    actor_id UUID NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    trace_id VARCHAR(128) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_work_order_event_work_order
        FOREIGN KEY (tenant_id, work_order_id) REFERENCES work_order(tenant_id, id),
    CONSTRAINT fk_work_order_event_actor FOREIGN KEY (actor_id) REFERENCES user_identity(id)
);

CREATE INDEX idx_work_order_event_tenant_work_order_created_at
    ON work_order_event(tenant_id, work_order_id, created_at);

CREATE TABLE idempotency_record (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    operation VARCHAR(64) NOT NULL,
    idempotency_key VARCHAR(200) NOT NULL,
    request_hash VARCHAR(128) NOT NULL,
    response_payload JSONB,
    status_code INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    CONSTRAINT fk_idempotency_record_tenant FOREIGN KEY (tenant_id) REFERENCES tenant(id),
    CONSTRAINT uq_idempotency_record_tenant_operation_key UNIQUE (tenant_id, operation, idempotency_key)
);

CREATE INDEX idx_idempotency_record_tenant_expires_at
    ON idempotency_record(tenant_id, expires_at);

CREATE TABLE outbox_event (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    aggregate_id UUID NOT NULL,
    aggregate_type VARCHAR(64) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMP,
    attempts INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT fk_outbox_event_tenant FOREIGN KEY (tenant_id) REFERENCES tenant(id),
    CONSTRAINT ck_outbox_event_status CHECK (status IN ('PENDING', 'PUBLISHED', 'FAILED')),
    CONSTRAINT ck_outbox_event_attempts CHECK (attempts >= 0)
);

CREATE INDEX idx_outbox_event_tenant_status_available_at
    ON outbox_event(tenant_id, status, available_at);

CREATE TABLE inbox_message (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    provider VARCHAR(64) NOT NULL,
    external_message_id VARCHAR(200) NOT NULL,
    message_type VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    CONSTRAINT fk_inbox_message_tenant FOREIGN KEY (tenant_id) REFERENCES tenant(id),
    CONSTRAINT uq_inbox_message_tenant_provider_external_id
        UNIQUE (tenant_id, provider, external_message_id)
);

CREATE INDEX idx_inbox_message_tenant_processed_at ON inbox_message(tenant_id, processed_at);
