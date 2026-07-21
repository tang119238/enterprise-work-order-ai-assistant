"""Tests for the analytics API endpoint."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from fastapi.testclient import TestClient

from app.analytics.models import AnalyticsQueryResponse
from app.analytics.router import router
from app.main import create_app


@pytest.fixture
def mock_tenant_id():
    return UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def app_with_analytics(mock_tenant_id):
    """Create test app with analytics router and mocked auth."""

    def mock_tenant_resolver(request):
        return mock_tenant_id

    app = create_app(tenant_resolver=mock_tenant_resolver)
    return app


@pytest.fixture
def client(app_with_analytics):
    return TestClient(app_with_analytics)


class TestAnalyticsAuth:
    def test_unauthenticated_returns_401(self):
        app = create_app()
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/analytics/query", json={"question": "test"})
        assert resp.status_code in (401, 403)

    def test_missing_roles_returns_403(self, mock_tenant_id):
        def mock_resolver(request):
            request.state.tenant_id = mock_tenant_id
            request.state.roles = []
            request.state.project_ids = []
            return mock_tenant_id

        app = create_app(tenant_resolver=mock_resolver)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/analytics/query",
            json={"question": "test"},
            headers={"Authorization": "Bearer fake-token"},
        )
        assert resp.status_code == 403

    def test_empty_project_scope_returns_403(self, mock_tenant_id):
        def mock_resolver(request):
            request.state.tenant_id = mock_tenant_id
            request.state.roles = ["ANALYST"]
            request.state.project_ids = []
            return mock_tenant_id

        app = create_app(tenant_resolver=mock_resolver)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/analytics/query",
            json={"question": "test"},
            headers={"Authorization": "Bearer fake-token"},
        )
        assert resp.status_code == 403


class TestAnalyticsRequest:
    def test_empty_question_rejected(self):
        """Empty question should be rejected by Pydantic validation."""
        # This tests the request validation
        from app.analytics.router import AnalyticsQueryRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AnalyticsQueryRequest(question="")

    def test_long_question_rejected(self):
        from app.analytics.router import AnalyticsQueryRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AnalyticsQueryRequest(question="x" * 1001)


class TestAnalyticsResponse:
    def test_response_model(self):
        resp = AnalyticsQueryResponse(
            answer="test answer",
            sql="SELECT 1",
            columns=["col1"],
            rows=[[1]],
            truncated=False,
            audit_id="test-id",
            latency_ms=100,
        )
        assert resp.answer == "test answer"
        assert resp.truncated is False
