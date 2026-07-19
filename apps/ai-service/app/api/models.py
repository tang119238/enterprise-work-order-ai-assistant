from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Primitive = str | int | float | bool | None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("session_id", "message")
    @classmethod
    def strip_and_reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class Citation(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    title: str
    section: str
    quote: str


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    arguments: dict[str, Primitive]
    status: Literal["success", "error"]


class ModelMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    name: str
    fallback: bool
    error_code: str | None = None


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    tool_calls: list[ToolCallRecord]
    latency_ms: int
    model: ModelMetadata
    retrieval_mode: Literal["hybrid", "bm25", "vector", "none"] = "none"
    warnings: list[str]


class WorkOrderRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    work_order_no: str
    title: str
    description: str | None = None
    project_name: str
    space_path: str | None = None
    order_type: str | None = None
    priority: str
    status: str
    assignee_name: str | None = None
    source: str | None = None
    root_work_order_no: str | None = None
    rework_reason: str | None = None
    created_at: datetime
    due_at: datetime
    completed_at: datetime | None = None


class WorkOrderSearchPage(BaseModel):
    items: list[WorkOrderRecord]
    page: int
    size: int
    total: int
    total_pages: int
