"""Versioned semantic catalog for NL2SQL queries.

The catalog defines views, columns, types, synonyms, allowed functions,
and JOIN paths. It is generated from code, not from database metadata,
to prevent prompt injection from altering the schema surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

CATALOG_VERSION = "1.0.0"


class DataType(str, Enum):
    UUID = "uuid"
    TEXT = "text"
    VARCHAR = "varchar"
    INTEGER = "integer"
    BIGINT = "bigint"
    NUMERIC = "numeric"
    BOOLEAN = "boolean"
    TIMESTAMP = "timestamp"
    TIMESTAMPTZ = "timestamptz"
    INTERVAL = "interval"


@dataclass(frozen=True)
class ColumnDef:
    name: str
    data_type: DataType
    description: str
    synonyms: tuple[str, ...] = ()
    enum_values: tuple[str, ...] = ()
    is_filterable: bool = True
    is_groupable: bool = True
    is_orderable: bool = True


@dataclass(frozen=True)
class ViewDef:
    name: str
    description: str
    columns: tuple[ColumnDef, ...]
    allowed_joins: tuple[str, ...] = ()


@dataclass(frozen=True)
class JoinDef:
    left_view: str
    right_view: str
    join_type: str  # INNER, LEFT
    on_clause: str
    description: str


@dataclass(frozen=True)
class FunctionDef:
    name: str
    description: str
    allowed_on_types: tuple[DataType, ...] = ()


@dataclass(frozen=True)
class SemanticCatalog:
    version: str
    views: tuple[ViewDef, ...]
    joins: tuple[JoinDef, ...]
    functions: tuple[FunctionDef, ...]
    max_rows: int = 200
    max_columns: int = 50
    max_result_bytes: int = 1_048_576  # 1 MB

    def get_view(self, name: str) -> ViewDef | None:
        for v in self.views:
            if v.name == name:
                return v
        return None

    def get_column(self, view_name: str, column_name: str) -> ColumnDef | None:
        view = self.get_view(view_name)
        if view is None:
            return None
        for c in view.columns:
            if c.name == column_name:
                return c
        return None

    def is_valid_view(self, name: str) -> bool:
        return any(v.name == name for v in self.views)

    def is_valid_column(self, view_name: str, column_name: str) -> bool:
        return self.get_column(view_name, column_name) is not None

    def is_valid_function(self, name: str) -> bool:
        return any(f.name.lower() == name.lower() for f in self.functions)


def build_catalog() -> SemanticCatalog:
    """Build the production semantic catalog."""
    return SemanticCatalog(
        version=CATALOG_VERSION,
        views=(
            ViewDef(
                name="analytics_work_order_v",
                description="工单统计视图，包含工单基本信息和派单时效",
                columns=(
                    ColumnDef("id", DataType.UUID, "工单内部ID", is_filterable=False, is_groupable=False),
                    ColumnDef("tenant_id", DataType.UUID, "租户ID", is_filterable=False, is_groupable=False),
                    ColumnDef("project_id", DataType.UUID, "项目ID", synonyms=("项目",)),
                    ColumnDef("work_order_no", DataType.VARCHAR, "工单编号", synonyms=("编号", "工单号")),
                    ColumnDef("title", DataType.VARCHAR, "工单标题", synonyms=("标题",)),
                    ColumnDef("project_name", DataType.VARCHAR, "项目名称", synonyms=("项目名",)),
                    ColumnDef("order_type", DataType.VARCHAR, "工单类型", synonyms=("类型",), enum_values=("BUG", "FEATURE", "IMPROVEMENT", "INCIDENT", "TASK")),
                    ColumnDef("priority", DataType.VARCHAR, "优先级", synonyms=("紧急程度",), enum_values=("P0", "P1", "P2", "P3")),
                    ColumnDef("status", DataType.VARCHAR, "状态", synonyms=("工单状态",), enum_values=("PENDING_DISPATCH", "PENDING_ACCEPTANCE", "PROCESSING", "COMPLETED", "CLOSED", "CANCELLED")),
                    ColumnDef("source", DataType.VARCHAR, "来源", synonyms=("渠道",), enum_values=("MANUAL", "API", "EMAIL", "MONITORING")),
                    ColumnDef("assignee_name", DataType.VARCHAR, "负责人", synonyms=("处理人", "经办人")),
                    ColumnDef("root_work_order_no", DataType.VARCHAR, "根工单编号", synonyms=("关联工单",)),
                    ColumnDef("created_at", DataType.TIMESTAMP, "创建时间", synonyms=("创建日期",)),
                    ColumnDef("due_at", DataType.TIMESTAMP, "截止时间", synonyms=("截止日期", "截止")),
                    ColumnDef("accepted_at", DataType.TIMESTAMP, "接单时间"),
                    ColumnDef("completed_at", DataType.TIMESTAMP, "完成时间", synonyms=("完成日期",)),
                    ColumnDef("cancelled_at", DataType.TIMESTAMP, "取消时间"),
                    ColumnDef("completion_hours", DataType.NUMERIC, "完成耗时(小时)", synonyms=("处理时长", "耗时")),
                    ColumnDef("is_overdue", DataType.BOOLEAN, "是否超期", synonyms=("超期", "逾期")),
                ),
            ),
            ViewDef(
                name="analytics_quality_v",
                description="质检结果视图，包含质检判定、模型信息和发现统计",
                columns=(
                    ColumnDef("id", DataType.UUID, "质检结果ID", is_filterable=False, is_groupable=False),
                    ColumnDef("tenant_id", DataType.UUID, "租户ID", is_filterable=False, is_groupable=False),
                    ColumnDef("project_id", DataType.UUID, "项目ID"),
                    ColumnDef("work_order_id", DataType.UUID, "工单ID", is_filterable=False, is_groupable=False),
                    ColumnDef("work_order_no", DataType.VARCHAR, "工单编号", synonyms=("编号",)),
                    ColumnDef("work_order_title", DataType.VARCHAR, "工单标题"),
                    ColumnDef("project_name", DataType.VARCHAR, "项目名称"),
                    ColumnDef("verdict", DataType.VARCHAR, "质检判定", synonyms=("结果", "判定"), enum_values=("PASS", "FAIL", "UNCERTAIN", "SKIP")),
                    ColumnDef("confidence", DataType.NUMERIC, "置信度"),
                    ColumnDef("inspection_round", DataType.INTEGER, "检查轮次", synonyms=("轮次",)),
                    ColumnDef("generated_at", DataType.TIMESTAMPTZ, "质检时间", synonyms=("检查时间",)),
                    ColumnDef("model_provider", DataType.VARCHAR, "模型提供商"),
                    ColumnDef("model_name", DataType.VARCHAR, "模型名称"),
                    ColumnDef("prompt_version", DataType.VARCHAR, "提示词版本"),
                    ColumnDef("model_latency_ms", DataType.INTEGER, "模型耗时(ms)"),
                    ColumnDef("finding_count", DataType.BIGINT, "发现总数", synonyms=("问题数",)),
                    ColumnDef("high_severity_count", DataType.BIGINT, "高严重度发现数"),
                    ColumnDef("medium_severity_count", DataType.BIGINT, "中严重度发现数"),
                    ColumnDef("low_severity_count", DataType.BIGINT, "低严重度发现数"),
                ),
            ),
            ViewDef(
                name="analytics_rectification_v",
                description="整改案例视图，包含整改状态和处理时效",
                columns=(
                    ColumnDef("id", DataType.UUID, "整改案例ID", is_filterable=False, is_groupable=False),
                    ColumnDef("tenant_id", DataType.UUID, "租户ID", is_filterable=False, is_groupable=False),
                    ColumnDef("project_id", DataType.UUID, "项目ID"),
                    ColumnDef("original_work_order_id", DataType.UUID, "原始工单ID", is_filterable=False, is_groupable=False),
                    ColumnDef("original_work_order_no", DataType.VARCHAR, "原始工单编号", synonyms=("工单编号",)),
                    ColumnDef("original_work_order_title", DataType.VARCHAR, "原始工单标题"),
                    ColumnDef("project_name", DataType.VARCHAR, "项目名称"),
                    ColumnDef("rectification_work_order_id", DataType.UUID, "整复工单ID", is_filterable=False, is_groupable=False),
                    ColumnDef("rectification_work_order_no", DataType.VARCHAR, "整复工单编号"),
                    ColumnDef("current_verdict", DataType.VARCHAR, "当前判定", synonyms=("判定",), enum_values=("PASS", "FAIL", "UNCERTAIN")),
                    ColumnDef("inspection_round", DataType.INTEGER, "检查轮次", synonyms=("轮次",)),
                    ColumnDef("status", DataType.VARCHAR, "整改状态", synonyms=("状态",), enum_values=("PROPOSED", "RECTIFYING", "RECHECKING", "CLOSED")),
                    ColumnDef("created_at", DataType.TIMESTAMPTZ, "创建时间"),
                    ColumnDef("closed_at", DataType.TIMESTAMPTZ, "关闭时间"),
                    ColumnDef("resolution_hours", DataType.NUMERIC, "解决耗时(小时)", synonyms=("处理时长",)),
                ),
            ),
        ),
        joins=(
            JoinDef(
                left_view="analytics_work_order_v",
                right_view="analytics_quality_v",
                join_type="LEFT",
                on_clause="analytics_work_order_v.tenant_id = analytics_quality_v.tenant_id AND analytics_work_order_v.work_order_no = analytics_quality_v.work_order_no",
                description="工单关联质检结果",
            ),
            JoinDef(
                left_view="analytics_work_order_v",
                right_view="analytics_rectification_v",
                join_type="LEFT",
                on_clause="analytics_work_order_v.tenant_id = analytics_rectification_v.tenant_id AND analytics_work_order_v.work_order_no = analytics_rectification_v.original_work_order_no",
                description="工单关联整改案例",
            ),
        ),
        functions=(
            FunctionDef("COUNT", "计数", (DataType.UUID, DataType.VARCHAR, DataType.INTEGER, DataType.BIGINT, DataType.BOOLEAN, DataType.TIMESTAMP, DataType.TIMESTAMPTZ)),
            FunctionDef("SUM", "求和", (DataType.INTEGER, DataType.BIGINT, DataType.NUMERIC)),
            FunctionDef("AVG", "平均值", (DataType.INTEGER, DataType.BIGINT, DataType.NUMERIC)),
            FunctionDef("MIN", "最小值", (DataType.INTEGER, DataType.BIGINT, DataType.NUMERIC, DataType.VARCHAR, DataType.TIMESTAMP, DataType.TIMESTAMPTZ)),
            FunctionDef("MAX", "最大值", (DataType.INTEGER, DataType.BIGINT, DataType.NUMERIC, DataType.VARCHAR, DataType.TIMESTAMP, DataType.TIMESTAMPTZ)),
            FunctionDef("COALESCE", "空值替代", (DataType.UUID, DataType.VARCHAR, DataType.INTEGER, DataType.BIGINT, DataType.NUMERIC, DataType.BOOLEAN, DataType.TIMESTAMP, DataType.TIMESTAMPTZ)),
            FunctionDef("DATE_TRUNC", "时间截断", (DataType.TIMESTAMP, DataType.TIMESTAMPTZ)),
            FunctionDef("EXTRACT", "提取时间分量", (DataType.TIMESTAMP, DataType.TIMESTAMPTZ, DataType.INTERVAL)),
            FunctionDef("UPPER", "转大写", (DataType.VARCHAR, DataType.TEXT)),
            FunctionDef("LOWER", "转小写", (DataType.VARCHAR, DataType.TEXT)),
            FunctionDef("TRIM", "去空格", (DataType.VARCHAR, DataType.TEXT)),
            FunctionDef("LENGTH", "字符串长度", (DataType.VARCHAR, DataType.TEXT)),
        ),
    )


# Singleton catalog instance
_catalog: SemanticCatalog | None = None


def get_catalog() -> SemanticCatalog:
    global _catalog
    if _catalog is None:
        _catalog = build_catalog()
    return _catalog
