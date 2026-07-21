"""SQL generation from natural language using the LLM gateway.

Uses the semantic catalog to construct a prompt that guides the model
to generate valid PostgreSQL SELECT statements.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..llm.gateway import LLMGateway
from .catalog import SemanticCatalog, get_catalog

logger = logging.getLogger(__name__)


@dataclass
class PlanResult:
    """Result of SQL planning."""
    sql: str
    model_provider: str
    model_name: str


def build_catalog_prompt(catalog: SemanticCatalog) -> str:
    """Build a schema description for the model prompt."""
    lines = [
        "You are a PostgreSQL SQL generator for a work order analytics system.",
        "Generate ONLY a single SELECT query. No explanations, no markdown, just SQL.",
        "",
        "## Available Views",
        "",
    ]

    for view in catalog.views:
        lines.append(f"### {view.name}")
        lines.append(f"Description: {view.description}")
        lines.append("Columns:")
        for col in view.columns:
            enums = f" (values: {', '.join(col.enum_values)})" if col.enum_values else ""
            syns = f" (Chinese synonyms: {', '.join(col.synonyms)})" if col.synonyms else ""
            lines.append(f"  - {col.name}: {col.data_type.value}{enums}{syns}")
        lines.append("")

    lines.append("## Allowed JOINs")
    lines.append("")
    for join in catalog.joins:
        lines.append(f"- {join.left_view} {join.join_type} JOIN {join.right_view} ON {join.on_clause}")
    lines.append("")

    lines.append("## Rules")
    lines.append("- Only use the views and columns listed above")
    lines.append("- tenant_id and project_id are filtered automatically, do NOT include them in WHERE")
    lines.append("- Use Chinese-aware comparisons with ILIKE for text search")
    lines.append("- Maximum 200 rows (LIMIT will be added automatically)")
    lines.append("- No subqueries, UNION, INTERSECT, EXCEPT, FOR UPDATE")
    lines.append("- No comments (-- or /* */)")
    lines.append("- No system tables or schemas (pg_catalog, information_schema)")
    lines.append("- Output ONLY the SQL, no explanation text")

    return "\n".join(lines)


async def generate_sql(
    question: str,
    gateway: LLMGateway,
    catalog: SemanticCatalog | None = None,
) -> PlanResult:
    """Generate SQL from a natural language question."""
    if catalog is None:
        catalog = get_catalog()

    prompt = build_catalog_prompt(catalog)
    full_prompt = f"{prompt}\n\n## Question\n{question}\n\n## SQL"

    response = await gateway.generate(
        prompt=full_prompt,
        system="You are a SQL generation assistant. Output only valid PostgreSQL SELECT SQL.",
    )

    # Extract SQL from response (strip markdown if present)
    sql = response.text.strip()
    if sql.startswith("```"):
        # Remove markdown code block
        lines = sql.split("\n")
        sql = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        sql = sql.strip()

    return PlanResult(
        sql=sql,
        model_provider=response.provider,
        model_name=response.model_name,
    )
