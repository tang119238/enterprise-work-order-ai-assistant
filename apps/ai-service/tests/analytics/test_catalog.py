"""Tests for the semantic catalog."""

from app.analytics.catalog import (
    CATALOG_VERSION,
    DataType,
    SemanticCatalog,
    build_catalog,
    get_catalog,
)


def test_catalog_version():
    catalog = get_catalog()
    assert catalog.version == CATALOG_VERSION


def test_catalog_has_three_views():
    catalog = get_catalog()
    assert len(catalog.views) == 3
    names = {v.name for v in catalog.views}
    assert names == {
        "analytics_work_order_v",
        "analytics_quality_v",
        "analytics_rectification_v",
    }


def test_work_order_view_columns():
    catalog = get_catalog()
    view = catalog.get_view("analytics_work_order_v")
    assert view is not None
    col_names = {c.name for c in view.columns}
    assert "work_order_no" in col_names
    assert "status" in col_names
    assert "priority" in col_names
    assert "tenant_id" in col_names


def test_quality_view_has_verdict():
    catalog = get_catalog()
    view = catalog.get_view("analytics_quality_v")
    assert view is not None
    verdict_col = catalog.get_column("analytics_quality_v", "verdict")
    assert verdict_col is not None
    assert "PASS" in verdict_col.enum_values
    assert "FAIL" in verdict_col.enum_values


def test_rectification_view_has_status():
    catalog = get_catalog()
    col = catalog.get_column("analytics_rectification_v", "status")
    assert col is not None
    assert "PROPOSED" in col.enum_values
    assert "CLOSED" in col.enum_values


def test_is_valid_view():
    catalog = get_catalog()
    assert catalog.is_valid_view("analytics_work_order_v") is True
    assert catalog.is_valid_view("work_order") is False
    assert catalog.is_valid_view("pg_class") is False


def test_is_valid_column():
    catalog = get_catalog()
    assert catalog.is_valid_column("analytics_work_order_v", "status") is True
    assert catalog.is_valid_column("analytics_work_order_v", "description") is False
    assert catalog.is_valid_column("nonexistent_view", "id") is False


def test_is_valid_function():
    catalog = get_catalog()
    assert catalog.is_valid_function("COUNT") is True
    assert catalog.is_valid_function("AVG") is True
    assert catalog.is_valid_function("pg_sleep") is False


def test_allowed_joins():
    catalog = get_catalog()
    assert len(catalog.joins) == 2
    join_views = {(j.left_view, j.right_view) for j in catalog.joins}
    assert ("analytics_work_order_v", "analytics_quality_v") in join_views


def test_get_nonexistent_view():
    catalog = get_catalog()
    assert catalog.get_view("nonexistent") is None
    assert catalog.get_column("nonexistent", "col") is None


def test_singleton_catalog():
    c1 = get_catalog()
    c2 = get_catalog()
    assert c1 is c2
