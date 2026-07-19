"""Create tenant-isolated quality inspection persistence.

Revision ID: 20260718_02
Revises: 20260718_01
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "20260718_02"
down_revision: str | None = "20260718_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TENANT_SETTING = "nullif(current_setting('app.tenant_id', true), '')::uuid"
QUALITY_TABLES = (
    "quality_job",
    "model_call_audit",
    "quality_result",
    "quality_finding",
)
APPEND_ONLY_TABLES = (
    "model_call_audit",
    "quality_result",
    "quality_finding",
)


def _timestamps() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def _enable_rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"""
            CREATE POLICY {table}_tenant_policy ON {table}
            USING (tenant_id = {TENANT_SETTING})
            WITH CHECK (tenant_id = {TENANT_SETTING})
            """
        )
    )


def upgrade() -> None:
    op.create_table(
        "quality_job",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("work_order_id", sa.Uuid(), nullable=False),
        sa.Column("work_order_version", sa.BigInteger(), nullable=False),
        sa.Column("inspection_round", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("business_key", sa.String(length=300), nullable=False),
        sa.Column("trigger_source", sa.String(length=64), nullable=False),
        sa.Column(
            "trigger_payload",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default=sa.text("100")),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_retry_count", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("result_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_quality_job"),
        sa.UniqueConstraint(
            "tenant_id",
            "business_key",
            name="uq_quality_job_tenant_business_key",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "work_order_id",
            "work_order_version",
            "inspection_round",
            name="uq_quality_job_business_key",
        ),
        sa.ForeignKeyConstraint(
            ("tenant_id",),
            ("tenant.id",),
            name="fk_quality_job_tenant",
        ),
        sa.ForeignKeyConstraint(
            ("tenant_id", "work_order_id"),
            ("work_order.tenant_id", "work_order.id"),
            name="fk_quality_job_work_order",
        ),
        sa.CheckConstraint("work_order_version >= 0", name="ck_quality_job_work_order_version"),
        sa.CheckConstraint("inspection_round > 0", name="ck_quality_job_inspection_round"),
        sa.CheckConstraint("btrim(business_key) <> ''", name="ck_quality_job_business_key"),
        sa.CheckConstraint("btrim(trigger_source) <> ''", name="ck_quality_job_trigger_source"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'RETRY_WAIT', 'SUCCEEDED', 'FAILED', 'SKIPPED')",
            name="ck_quality_job_status",
        ),
        sa.CheckConstraint("priority >= 0", name="ck_quality_job_priority"),
        sa.CheckConstraint("retry_count >= 0", name="ck_quality_job_retry_count"),
        sa.CheckConstraint("max_retry_count >= 0", name="ck_quality_job_max_retry_count"),
        sa.CheckConstraint(
            "retry_count <= max_retry_count",
            name="ck_quality_job_retry_limit",
        ),
        sa.CheckConstraint(
            "status <> 'RETRY_WAIT' OR next_retry_at IS NOT NULL",
            name="ck_quality_job_retry_schedule",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_quality_job_execution_interval",
        ),
    )
    op.create_index(
        "idx_quality_job_tenant_status_retry",
        "quality_job",
        ("tenant_id", "status", "next_retry_at", "priority"),
    )
    op.create_index(
        "idx_quality_job_tenant_work_order",
        "quality_job",
        ("tenant_id", "work_order_id", "created_at"),
    )

    op.create_table(
        "model_call_audit",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("quality_job_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("request_id", sa.String(length=200), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("input_summary", JSONB(), nullable=False),
        sa.Column("response_summary", JSONB(), nullable=False),
        sa.Column("raw_response_truncated", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_model_call_audit"),
        sa.UniqueConstraint("tenant_id", "request_id", name="uq_model_call_audit_request"),
        sa.ForeignKeyConstraint(
            ("tenant_id", "quality_job_id"),
            ("quality_job.tenant_id", "quality_job.id"),
            name="fk_model_call_audit_job",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("btrim(provider) <> ''", name="ck_model_call_audit_provider"),
        sa.CheckConstraint("btrim(model_name) <> ''", name="ck_model_call_audit_model"),
        sa.CheckConstraint("btrim(prompt_version) <> ''", name="ck_model_call_audit_prompt"),
        sa.CheckConstraint("btrim(request_id) <> ''", name="ck_model_call_audit_request"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_model_call_audit_latency"),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_model_call_audit_input_tokens",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_model_call_audit_output_tokens",
        ),
        sa.CheckConstraint(
            "estimated_cost IS NULL OR estimated_cost >= 0",
            name="ck_model_call_audit_cost",
        ),
    )
    op.create_index(
        "idx_model_call_audit_tenant_job",
        "model_call_audit",
        ("tenant_id", "quality_job_id", "created_at"),
    )

    op.create_table(
        "quality_result",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("quality_job_id", sa.Uuid(), nullable=False),
        sa.Column("work_order_id", sa.Uuid(), nullable=False),
        sa.Column("work_order_version", sa.BigInteger(), nullable=False),
        sa.Column("inspection_round", sa.Integer(), nullable=False),
        sa.Column("model_call_id", sa.Uuid(), nullable=True),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("work_order_snapshot", JSONB(), nullable=False),
        sa.Column("policy_versions", JSONB(), nullable=False),
        sa.Column("attachment_summary", JSONB(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "callback_state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column("callback_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_quality_result"),
        sa.UniqueConstraint("quality_job_id", name="uq_quality_result_job"),
        sa.ForeignKeyConstraint(
            ("tenant_id", "quality_job_id"),
            ("quality_job.tenant_id", "quality_job.id"),
            name="fk_quality_result_job",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ("tenant_id", "work_order_id"),
            ("work_order.tenant_id", "work_order.id"),
            name="fk_quality_result_work_order",
        ),
        sa.ForeignKeyConstraint(
            ("tenant_id", "model_call_id"),
            ("model_call_audit.tenant_id", "model_call_audit.id"),
            name="fk_quality_result_model_call",
        ),
        sa.CheckConstraint("work_order_version >= 0", name="ck_quality_result_version"),
        sa.CheckConstraint("inspection_round > 0", name="ck_quality_result_round"),
        sa.CheckConstraint(
            "verdict IN ('PASS', 'FAIL', 'UNCERTAIN', 'SKIP')",
            name="ck_quality_result_verdict",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_quality_result_confidence",
        ),
        sa.CheckConstraint(
            "callback_state IN ('PENDING', 'DELIVERED', 'FAILED')",
            name="ck_quality_result_callback_state",
        ),
        sa.CheckConstraint(
            "callback_state <> 'DELIVERED' OR callback_at IS NOT NULL",
            name="ck_quality_result_callback_delivery",
        ),
    )
    op.create_index(
        "idx_quality_result_tenant_work_order",
        "quality_result",
        ("tenant_id", "work_order_id", "generated_at"),
    )

    op.create_table(
        "quality_finding",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("quality_result_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("rule_code", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("evidence", JSONB(), nullable=False),
        sa.Column("policy_chunk_id", sa.Uuid(), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_quality_finding"),
        sa.UniqueConstraint(
            "tenant_id",
            "quality_result_id",
            "ordinal",
            name="uq_quality_finding_result_ordinal",
        ),
        sa.ForeignKeyConstraint(
            ("tenant_id", "quality_result_id"),
            ("quality_result.tenant_id", "quality_result.id"),
            name="fk_quality_finding_result",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_quality_finding_ordinal"),
        sa.CheckConstraint("btrim(rule_code) <> ''", name="ck_quality_finding_rule"),
        sa.CheckConstraint(
            "severity IN ('LOW', 'MEDIUM', 'HIGH')",
            name="ck_quality_finding_severity",
        ),
        sa.CheckConstraint(
            "label IN ('PASS', 'FAIL', 'UNCERTAIN', 'SKIP')",
            name="ck_quality_finding_label",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_quality_finding_confidence",
        ),
        sa.CheckConstraint(
            "source IN ('RULE', 'MODEL')",
            name="ck_quality_finding_source",
        ),
    )
    op.create_index(
        "idx_quality_finding_tenant_result",
        "quality_finding",
        ("tenant_id", "quality_result_id", "severity"),
    )

    op.create_foreign_key(
        "fk_quality_job_result",
        "quality_job",
        "quality_result",
        ("tenant_id", "result_id"),
        ("tenant_id", "id"),
    )

    for table in QUALITY_TABLES:
        _enable_rls(table)
        op.execute(
            sa.text(
                f"REVOKE ALL PRIVILEGES ON TABLE {table} "
                "FROM PUBLIC, work_order_app, analytics_reader"
            )
        )
    op.execute(sa.text("GRANT SELECT, INSERT, UPDATE ON TABLE quality_job TO ai_app"))
    for table in APPEND_ONLY_TABLES:
        op.execute(sa.text(f"GRANT SELECT, INSERT ON TABLE {table} TO ai_app"))


def downgrade() -> None:
    op.drop_constraint("fk_quality_job_result", "quality_job", type_="foreignkey")
    op.drop_table("quality_finding")
    op.drop_table("quality_result")
    op.drop_table("model_call_audit")
    op.drop_table("quality_job")
