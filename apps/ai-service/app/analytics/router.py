"""FastAPI router for NL2SQL analytics endpoint."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .client import AnalyticsClient
from .service import AnalyticsService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])


class AnalyticsQueryRequest(BaseModel):
    """Natural language analytics query."""
    question: str = Field(..., min_length=1, max_length=1000, description="自然语言分析问题")


class AnalyticsQueryResponse(BaseModel):
    """Structured analytics query result."""
    answer: str = Field(..., description="确定性汇总或模型解释")
    sql: str = Field(..., description="实际执行的 SQL")
    columns: list[str] = Field(default_factory=list, description="列名列表")
    rows: list[list[object]] = Field(default_factory=list, description="行数据")
    truncated: bool = Field(default=False, description="是否截断")
    audit_id: str = Field(..., description="审计记录ID")
    latency_ms: int = Field(..., description="总耗时毫秒")


def _get_tenant_id(request: Request) -> UUID:
    """Extract authenticated tenant ID from request state."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not isinstance(tenant_id, UUID):
        raise HTTPException(
            status_code=401,
            detail={"error_code": "AUTHENTICATED_TENANT_REQUIRED", "message": "需要认证"},
        )
    return tenant_id


def _require_analyst_role(request: Request) -> None:
    """Check that user has ANALYST role."""
    roles = getattr(request.state, "roles", [])
    if "ANALYST" not in roles:
        raise HTTPException(
            status_code=403,
            detail={"error_code": "ANALYTICS_NOT_PERMITTED", "message": "需要 ANALYST 角色"},
        )
    project_ids = getattr(request.state, "project_ids", [])
    if not project_ids:
        raise HTTPException(
            status_code=403,
            detail={"error_code": "ANALYTICS_NOT_PERMITTED", "message": "项目范围为空"},
        )


@router.post("/query", response_model=AnalyticsQueryResponse)
async def analytics_query(
    req: AnalyticsQueryRequest,
    request: Request,
) -> AnalyticsQueryResponse:
    """Execute a natural language analytics query.

    Requires ANALYST role and non-empty project scope.
    Returns structured result with SQL, columns, rows, and audit ID.
    """
    _require_analyst_role(request)
    tenant_id = _get_tenant_id(request)

    user_id = getattr(request.state, "user_id", str(tenant_id))
    project_ids = getattr(request.state, "project_ids", [])

    settings = request.app.state.settings
    gateway = request.app.state.gateway
    client = AnalyticsClient(settings)
    service = AnalyticsService(gateway, client)

    try:
        return await service.query(
            question=req.question,
            tenant_id=str(tenant_id),
            user_id=str(user_id),
            project_ids=[str(p) for p in project_ids],
        )
    except Exception as e:
        logger.exception("Analytics query failed")
        raise HTTPException(
            status_code=500,
            detail={"error_code": "INTERNAL_ERROR", "message": str(e)},
        )
