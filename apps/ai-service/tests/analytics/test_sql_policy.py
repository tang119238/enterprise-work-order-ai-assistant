"""Tests for the Python SQL policy validator."""

import pytest

from app.analytics.sql_policy import SqlPolicyViolation, validate_sql
from app.analytics.catalog import get_catalog


@pytest.fixture
def catalog():
    return get_catalog()


class TestValidQueries:
    def test_simple_select(self, catalog):
        sql = "SELECT work_order_no, status FROM analytics_work_order_v"
        result = validate_sql(sql, catalog)
        assert result.valid is True
        assert result.normalized_sql is not None
        assert "LIMIT" in result.normalized_sql

    def test_select_with_where(self, catalog):
        sql = "SELECT work_order_no, status FROM analytics_work_order_v WHERE status = 'CLOSED'"
        result = validate_sql(sql, catalog)
        assert result.valid is True

    def test_select_with_aggregate(self, catalog):
        sql = "SELECT status, COUNT(*) AS cnt FROM analytics_work_order_v GROUP BY status"
        result = validate_sql(sql, catalog)
        assert result.valid is True

    def test_select_with_join(self, catalog):
        sql = (
            "SELECT w.work_order_no, q.verdict "
            "FROM analytics_work_order_v w "
            "LEFT JOIN analytics_quality_v q "
            "ON w.tenant_id = q.tenant_id AND w.work_order_no = q.work_order_no"
        )
        result = validate_sql(sql, catalog)
        assert result.valid is True

    def test_select_with_order_by(self, catalog):
        sql = "SELECT work_order_no, created_at FROM analytics_work_order_v ORDER BY created_at DESC"
        result = validate_sql(sql, catalog)
        assert result.valid is True

    def test_select_with_limit(self, catalog):
        sql = "SELECT work_order_no FROM analytics_work_order_v LIMIT 10"
        result = validate_sql(sql, catalog)
        assert result.valid is True
        assert "10" in result.normalized_sql

    def test_limit_capped_at_200(self, catalog):
        sql = "SELECT work_order_no FROM analytics_work_order_v LIMIT 500"
        result = validate_sql(sql, catalog)
        assert result.valid is True
        assert "200" in result.normalized_sql

    def test_select_with_date_trunc(self, catalog):
        sql = (
            "SELECT DATE_TRUNC('month', created_at) AS month, COUNT(*) "
            "FROM analytics_work_order_v GROUP BY month"
        )
        result = validate_sql(sql, catalog)
        assert result.valid is True

    def test_select_with_coalesce(self, catalog):
        sql = "SELECT COALESCE(assignee_name, 'N/A') FROM analytics_work_order_v"
        result = validate_sql(sql, catalog)
        assert result.valid is True


class TestBlockedConstructs:
    def test_insert_blocked(self, catalog):
        sql = "INSERT INTO analytics_work_order_v (status) VALUES ('HACKED')"
        result = validate_sql(sql, catalog)
        assert result.valid is False

    def test_update_blocked(self, catalog):
        sql = "UPDATE analytics_work_order_v SET status = 'HACKED'"
        result = validate_sql(sql, catalog)
        assert result.valid is False

    def test_delete_blocked(self, catalog):
        sql = "DELETE FROM analytics_work_order_v"
        result = validate_sql(sql, catalog)
        assert result.valid is False

    def test_drop_blocked(self, catalog):
        sql = "DROP TABLE analytics_work_order_v"
        result = validate_sql(sql, catalog)
        assert result.valid is False

    def test_multiple_statements_blocked(self, catalog):
        sql = "SELECT 1; DROP TABLE analytics_work_order_v"
        result = validate_sql(sql, catalog)
        assert result.valid is False

    def test_line_comment_blocked(self, catalog):
        sql = "SELECT work_order_no --, status FROM analytics_work_order_v"
        result = validate_sql(sql, catalog)
        assert result.valid is False

    def test_block_comment_blocked(self, catalog):
        sql = "SELECT work_order_no /* , status */ FROM analytics_work_order_v"
        result = validate_sql(sql, catalog)
        assert result.valid is False

    def test_union_blocked(self, catalog):
        sql = (
            "SELECT work_order_no FROM analytics_work_order_v "
            "UNION ALL SELECT work_order_no FROM analytics_quality_v"
        )
        result = validate_sql(sql, catalog)
        assert result.valid is False


class TestBlockedTables:
    def test_pg_catalog_blocked(self, catalog):
        sql = "SELECT * FROM pg_catalog.pg_class"
        result = validate_sql(sql, catalog)
        assert result.valid is False

    def test_information_schema_blocked(self, catalog):
        sql = "SELECT * FROM information_schema.tables"
        result = validate_sql(sql, catalog)
        assert result.valid is False

    def test_system_table_blocked(self, catalog):
        sql = "SELECT * FROM pg_stat_activity"
        result = validate_sql(sql, catalog)
        assert result.valid is False

    def test_base_table_blocked(self, catalog):
        sql = "SELECT * FROM work_order"
        result = validate_sql(sql, catalog)
        assert result.valid is False


class TestBlockedFunctions:
    def test_pg_sleep_blocked(self, catalog):
        sql = "SELECT pg_sleep(10) FROM analytics_work_order_v"
        result = validate_sql(sql, catalog)
        assert result.valid is False


class TestEdgeCases:
    def test_empty_sql(self, catalog):
        result = validate_sql("", catalog)
        assert result.valid is False

    def test_whitespace_only(self, catalog):
        result = validate_sql("   ", catalog)
        assert result.valid is False

    def test_not_select(self, catalog):
        result = validate_sql("CREATE TABLE foo (id int)", catalog)
        assert result.valid is False

    def test_invalid_sql(self, catalog):
        result = validate_sql("SELEC * FORM analytics_work_order_v", catalog)
        assert result.valid is False
