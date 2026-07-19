"""Add a narrow callback-delivery mutation boundary.

Revision ID: 20260720_03
Revises: 20260718_02
Create Date: 2026-07-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260720_03"
down_revision: str | None = "20260718_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mark_quality_result_callback_delivered(
            p_tenant_id uuid,
            p_result_id uuid,
            p_delivered_at timestamptz
        ) RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
            changed boolean;
            current_tenant uuid;
        BEGIN
            current_tenant := nullif(current_setting('app.tenant_id', true), '')::uuid;
            IF current_tenant IS NULL OR current_tenant IS DISTINCT FROM p_tenant_id THEN
                RAISE EXCEPTION 'tenant context does not match callback result'
                    USING ERRCODE = '42501';
            END IF;
            IF p_delivered_at IS NULL THEN
                RAISE EXCEPTION 'callback delivery timestamp is required'
                    USING ERRCODE = '22004';
            END IF;

            UPDATE quality_result
            SET callback_state = 'DELIVERED', callback_at = p_delivered_at
            WHERE tenant_id = p_tenant_id
              AND id = p_result_id
              AND callback_at IS NULL;
            changed := FOUND;
            RETURN changed;
        END;
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION mark_quality_result_callback_delivered(uuid, uuid, timestamptz) "
        "FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "mark_quality_result_callback_delivered(uuid, uuid, timestamptz) TO ai_app"
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "mark_quality_result_callback_delivered(uuid, uuid, timestamptz)"
    )
