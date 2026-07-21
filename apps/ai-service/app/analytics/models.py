"""Pydantic models for NL2SQL analytics API."""

from __future__ import annotations

from pydantic import BaseModel, Field


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


class AnalyticsError(BaseModel):
    """Analytics error response."""
    error_code: str
    message: str
    audit_id: str | None = None


class InternalExecuteRequest(BaseModel):
    """Internal request to Java analytics executor."""
    sql: str
    catalog_version: str
    user_context: dict[str, object]


class InternalExecuteResponse(BaseModel):
    """Response from Java analytics executor."""
    columns: list[str]
    rows: list[list[object]]
    truncated: bool
    execution_ms: int
    row_count: int
    audit_id: str
