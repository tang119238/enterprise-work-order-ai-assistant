"""Attack corpus tests for NL2SQL safety.

Tests that dangerous SQL strings are blocked by Python validator.
"""

import pytest

from app.analytics.sql_policy import validate_sql
from app.analytics.catalog import get_catalog


@pytest.fixture
def catalog():
    return get_catalog()


# Attack corpus: SQL injection and abuse patterns
ATTACK_CASES = [
    ("INSERT INTO work_order (status) VALUES ('HACKED')", "DML INSERT"),
    ("UPDATE work_order SET status = 'CLOSED'", "DML UPDATE"),
    ("DELETE FROM work_order", "DML DELETE"),
    ("TRUNCATE TABLE work_order", "DML TRUNCATE"),
    ("DROP TABLE work_order", "DDL DROP"),
    ("CREATE TABLE evil (id int)", "DDL CREATE"),
    ("ALTER TABLE work_order ADD COLUMN evil text", "DDL ALTER"),
    ("SELECT * FROM pg_catalog.pg_class", "system table pg_class"),
    ("SELECT * FROM information_schema.tables", "information_schema"),
    ("SELECT * FROM pg_stat_activity", "pg_stat_activity"),
    ("SELECT * FROM pg_user", "pg_user"),
    ("SELECT pg_sleep(30) FROM analytics_work_order_v", "pg_sleep"),
    ("SELECT lo_import('/etc/passwd') FROM analytics_work_order_v", "lo_import"),
    ("SELECT 1; DROP TABLE work_order", "multi-statement"),
    ("SELECT 1 -- comment", "line comment injection"),
    ("SELECT 1 /* comment */", "block comment injection"),
    (
        "SELECT work_order_no FROM analytics_work_order_v "
        "UNION ALL SELECT password FROM pg_user",
        "UNION injection",
    ),
    (
        "WITH RECURSIVE r AS (SELECT 1 UNION ALL SELECT n+1 FROM r WHERE n < 1000000) SELECT * FROM r",
        "recursive CTE",
    ),
    ("COPY analytics_work_order_v TO '/tmp/dump.csv'", "COPY to file"),
]

# Legal queries that must pass
LEGAL_CASES = [
    ("SELECT work_order_no, status FROM analytics_work_order_v", "simple select"),
    ("SELECT status, COUNT(*) FROM analytics_work_order_v GROUP BY status", "aggregate"),
    ("SELECT priority, AVG(completion_hours) FROM analytics_work_order_v GROUP BY priority", "avg"),
    (
        "SELECT w.work_order_no, q.verdict FROM analytics_work_order_v w "
        "LEFT JOIN analytics_quality_v q ON w.tenant_id = q.tenant_id AND w.work_order_no = q.work_order_no",
        "allowed join",
    ),
    (
        "SELECT DATE_TRUNC('month', created_at) AS month, COUNT(*) "
        "FROM analytics_work_order_v GROUP BY month ORDER BY month",
        "date truncation",
    ),
    (
        "SELECT status, COUNT(*) AS cnt FROM analytics_work_order_v "
        "WHERE status IN ('COMPLETED', 'CLOSED') GROUP BY status ORDER BY cnt DESC",
        "filter and order",
    ),
]


class TestAttackCorpus:
    @pytest.mark.parametrize("sql,label", ATTACK_CASES, ids=[c[1] for c in ATTACK_CASES])
    def test_attack_blocked_by_python(self, sql, label, catalog):
        result = validate_sql(sql, catalog)
        assert result.valid is False, f"Attack not blocked: {label}"


class TestLegalCorpus:
    @pytest.mark.parametrize("sql,label", LEGAL_CASES, ids=[c[1] for c in LEGAL_CASES])
    def test_legal_query_passes_python(self, sql, label, catalog):
        result = validate_sql(sql, catalog)
        assert result.valid is True, f"Legal query rejected: {label}: {result.error}"
