"""Analytics service orchestration.

Coordinates SQL generation, validation, execution, and response assembly.
"""

from __future__ import annotations

import logging
import time
import uuid

from .catalog import get_catalog, CATALOG_VERSION
from .client import AnalyticsClient, AnalyticsClientError
from .models import AnalyticsQueryResponse
from .planner import generate_sql
from .sql_policy import validate_sql, SqlPolicyViolation
from ..llm.gateway import LLMGateway

logger = logging.getLogger(__name__)


class AnalyticsService:
    """Orchestrates NL2SQL query processing."""

    def __init__(self, gateway: LLMGateway, client: AnalyticsClient) -> None:
        self._gateway = gateway
        self._client = client
        self._catalog = get_catalog()

    async def query(
        self,
        question: str,
        tenant_id: str,
        user_id: str,
        project_ids: list[str],
    ) -> AnalyticsQueryResponse:
        """Process a natural language analytics query.

        Flow:
        1. Generate SQL from question using LLM
        2. Validate SQL against semantic catalog (Python layer)
        3. Send to Java for independent validation and execution
        4. Assemble structured response
        """
        start_time = time.monotonic()
        audit_id = str(uuid.uuid4())

        # Step 1: Generate SQL
        try:
            plan = await generate_sql(question, self._gateway, self._catalog)
        except Exception as e:
            logger.error("SQL generation failed: %s", e)
            return AnalyticsQueryResponse(
                answer=f"无法生成查询: {e}",
                sql="",
                columns=[],
                rows=[],
                truncated=False,
                audit_id=audit_id,
                latency_ms=int((time.monotonic() - start_time) * 1000),
            )

        # Step 2: Python-side validation
        validation = validate_sql(plan.sql, self._catalog)
        if not validation.valid:
            logger.warning("SQL policy violation: %s", validation.error)
            return AnalyticsQueryResponse(
                answer=f"生成的查询不符合安全策略: {validation.error}",
                sql=plan.sql,
                columns=[],
                rows=[],
                truncated=False,
                audit_id=audit_id,
                latency_ms=int((time.monotonic() - start_time) * 1000),
            )

        normalized_sql = validation.normalized_sql or plan.sql

        # Step 3: Execute via Java (with independent validation)
        try:
            result = await self._client.execute_sql(
                sql=normalized_sql,
                catalog_version=CATALOG_VERSION,
                tenant_id=tenant_id,
                user_id=user_id,
                project_ids=project_ids,
            )
        except AnalyticsClientError as e:
            logger.error("Analytics execution failed: %s", e)
            return AnalyticsQueryResponse(
                answer=f"查询执行失败: {e.message}",
                sql=normalized_sql,
                columns=[],
                rows=[],
                truncated=False,
                audit_id=e.audit_id or audit_id,
                latency_ms=int((time.monotonic() - start_time) * 1000),
            )

        # Step 4: Build deterministic summary
        answer = _build_summary(result.columns, result.rows, result.row_count)

        return AnalyticsQueryResponse(
            answer=answer,
            sql=normalized_sql,
            columns=result.columns,
            rows=result.rows,
            truncated=result.truncated,
            audit_id=result.audit_id,
            latency_ms=int((time.monotonic() - start_time) * 1000),
        )


def _build_summary(columns: list[str], rows: list[list[object]], row_count: int) -> str:
    """Build a deterministic text summary of query results.

    This summary is based only on actual returned data.
    """
    if row_count == 0:
        return "查询返回 0 行结果。"

    lines = [f"查询返回 {row_count} 行结果。"]

    # Format as a simple table for small result sets
    if row_count <= 20 and columns:
        # Header
        lines.append("")
        lines.append(" | ".join(columns))
        lines.append(" | ".join(["---"] * len(columns)))

        for row in rows[:20]:
            cells = [str(v) if v is not None else "NULL" for v in row]
            lines.append(" | ".join(cells))

        if row_count > 20:
            lines.append(f"... 还有 {row_count - 20} 行")

    return "\n".join(lines)
