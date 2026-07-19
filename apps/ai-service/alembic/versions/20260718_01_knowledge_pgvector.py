"""Create tenant-isolated pgvector knowledge persistence.

Revision ID: 20260718_01
Revises:
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR

from alembic import op

revision: str = "20260718_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TENANT_SETTING = "nullif(current_setting('app.tenant_id', true), '')::uuid"
KNOWLEDGE_TABLES = (
    "knowledge_document",
    "knowledge_chunk",
    "knowledge_embedding",
    "embedding_job",
)


def _audit_columns() -> tuple[sa.Column[object], sa.Column[object]]:
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
    # The PostgreSQL init bootstrap creates this as the admin user. Keeping the
    # idempotent statement here also supports databases provisioned by an admin
    # before Alembic is run by the non-superuser migration owner.
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))

    op.create_table(
        "knowledge_document",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_key", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column(
            "source_type",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'MARKDOWN'"),
        ),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_document"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_knowledge_document_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "document_key",
            "version",
            name="uq_knowledge_document_tenant_key_version",
        ),
        sa.ForeignKeyConstraint(
            ("tenant_id",),
            ("tenant.id",),
            name="fk_knowledge_document_tenant",
        ),
        sa.CheckConstraint("btrim(document_key) <> ''", name="ck_knowledge_document_key_nonblank"),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_knowledge_document_title_nonblank"),
        sa.CheckConstraint(
            "btrim(source_type) <> ''",
            name="ck_knowledge_document_source_nonblank",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_knowledge_document_content_hash",
        ),
        sa.CheckConstraint("version > 0", name="ck_knowledge_document_version"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ACTIVE', 'INACTIVE')",
            name="ck_knowledge_document_status",
        ),
    )

    op.create_table(
        "knowledge_chunk",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_key", sa.String(length=200), nullable=False),
        sa.Column("section", sa.String(length=500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_chunk"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_knowledge_chunk_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            "id",
            name="uq_knowledge_chunk_tenant_document_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            "chunk_key",
            name="uq_knowledge_chunk_tenant_document_key",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            "ordinal",
            name="uq_knowledge_chunk_tenant_document_ordinal",
        ),
        sa.ForeignKeyConstraint(
            ("tenant_id", "document_id"),
            ("knowledge_document.tenant_id", "knowledge_document.id"),
            name="fk_knowledge_chunk_tenant_document",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("btrim(chunk_key) <> ''", name="ck_knowledge_chunk_key_nonblank"),
        sa.CheckConstraint("btrim(content) <> ''", name="ck_knowledge_chunk_content_nonblank"),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_knowledge_chunk_content_hash",
        ),
        sa.CheckConstraint("token_count >= 0", name="ck_knowledge_chunk_token_count"),
        sa.CheckConstraint("ordinal >= 0", name="ck_knowledge_chunk_ordinal"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ACTIVE', 'INACTIVE')",
            name="ck_knowledge_chunk_status",
        ),
    )

    op.create_table(
        "knowledge_embedding",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("model_key", sa.String(length=200), nullable=False),
        sa.Column("dimensions", sa.SmallInteger(), nullable=False, server_default=sa.text("512")),
        sa.Column("embedding", VECTOR(dim=512), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "embedded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        *_audit_columns(),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "chunk_id",
            "model_key",
            name="pk_knowledge_embedding",
        ),
        sa.ForeignKeyConstraint(
            ("tenant_id", "chunk_id"),
            ("knowledge_chunk.tenant_id", "knowledge_chunk.id"),
            name="fk_knowledge_embedding_tenant_chunk",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("btrim(model_key) <> ''", name="ck_knowledge_embedding_model_nonblank"),
        sa.CheckConstraint("dimensions = 512", name="ck_knowledge_embedding_dimensions"),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_knowledge_embedding_content_hash",
        ),
    )
    op.create_index(
        "idx_knowledge_embedding_tenant_model",
        "knowledge_embedding",
        ("tenant_id", "model_key"),
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX idx_knowledge_embedding_hnsw
            ON knowledge_embedding
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
            """
        )
    )

    op.create_table(
        "embedding_job",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("business_key", sa.String(length=300), nullable=False),
        sa.Column("model_key", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_embedding_job"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_embedding_job_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "business_key",
            name="uq_embedding_job_tenant_business_key",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "chunk_id",
            "model_key",
            name="uq_embedding_job_tenant_chunk_model",
        ),
        sa.ForeignKeyConstraint(
            ("tenant_id", "document_id"),
            ("knowledge_document.tenant_id", "knowledge_document.id"),
            name="fk_embedding_job_tenant_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ("tenant_id", "document_id", "chunk_id"),
            (
                "knowledge_chunk.tenant_id",
                "knowledge_chunk.document_id",
                "knowledge_chunk.id",
            ),
            name="fk_embedding_job_tenant_document_chunk",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("btrim(business_key) <> ''", name="ck_embedding_job_business_nonblank"),
        sa.CheckConstraint("btrim(model_key) <> ''", name="ck_embedding_job_model_nonblank"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'RETRY_WAIT', 'SUCCEEDED', 'FAILED', 'SKIPPED')",
            name="ck_embedding_job_status",
        ),
        sa.CheckConstraint("retry_count >= 0", name="ck_embedding_job_retry_count"),
        sa.CheckConstraint(
            "status <> 'RETRY_WAIT' OR next_retry_at IS NOT NULL",
            name="ck_embedding_job_retry_schedule",
        ),
    )

    for table in KNOWLEDGE_TABLES:
        _enable_rls(table)
        op.execute(
            sa.text(
                f"REVOKE ALL PRIVILEGES ON TABLE {table} "
                "FROM PUBLIC, work_order_app, analytics_reader"
            )
        )
        op.execute(
            sa.text(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO ai_app"
            )
        )


def downgrade() -> None:
    op.drop_table("embedding_job")
    op.drop_table("knowledge_embedding")
    op.drop_table("knowledge_chunk")
    op.drop_table("knowledge_document")
    # The vector extension is shared infrastructure and intentionally retained.
