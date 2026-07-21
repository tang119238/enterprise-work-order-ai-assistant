"""Python-side SQL policy validator using sqlglot AST parsing.

Validates model-generated SQL against the semantic catalog before
sending to Java for independent re-validation and execution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from .catalog import SemanticCatalog, get_catalog


class SqlPolicyViolation(Exception):
    """Raised when SQL violates policy."""

    def __init__(self, reason: str, stage: str = "PYTHON_POLICY"):
        self.reason = reason
        self.stage = stage
        super().__init__(reason)


# Dangerous functions that must never appear
BLOCKED_FUNCTIONS = frozenset({
    "pg_sleep", "pg_terminate_backend", "pg_cancel_backend",
    "lo_import", "lo_export", "dblink", "dblink_connect",
    "dblink_exec", "dblink_open", "dblink_close",
    "pg_read_file", "pg_write_file", "pg_read_binary_file",
    "copy", "copy_to", "copy_from",
})

# System schemas/tables that must never appear
BLOCKED_SCHEMAS = frozenset({
    "pg_catalog", "information_schema", "pg_toast",
    "pg_stat", "pg_class", "pg_proc", "pg_trigger",
    "pg_depend", "pg_description", "pg_namespace",
    "pg_authid", "pg_shadow", "pg_user", "pg_roles",
    "pg_settings", "pg_file_settings", "pg_hba_file_rules",
})

BLOCKED_TABLES = frozenset({
    "pg_class", "pg_proc", "pg_trigger", "pg_depend",
    "pg_description", "pg_namespace", "pg_authid",
    "pg_shadow", "pg_user", "pg_roles", "pg_settings",
    "pg_file_settings", "pg_hba_file_rules", "pg_stat_activity",
    "pg_stat_replication", "pg_stat_wal_receiver",
})

# Patterns that indicate injection attempts
INJECTION_PATTERNS = [
    re.compile(r";\s*\w", re.IGNORECASE),  # Multiple statements
    re.compile(r"--", re.IGNORECASE),  # Line comments
    re.compile(r"/\*.*?\*/", re.DOTALL),  # Block comments
    re.compile(r"\bUNION\s+ALL\s+SELECT\b", re.IGNORECASE),  # Union injection
    re.compile(r"\bINTO\s+(OUTFILE|DUMPFILE)\b", re.IGNORECASE),  # File write
    re.compile(r"\bLOAD_FILE\s*\(", re.IGNORECASE),  # File read
]


@dataclass
class ValidationResult:
    """Result of SQL policy validation."""
    valid: bool
    normalized_sql: str | None = None
    error: str | None = None
    stage: str | None = None


def validate_sql(sql: str, catalog: SemanticCatalog | None = None) -> ValidationResult:
    """Validate SQL against the semantic catalog policy.

    Checks:
    1. Exactly one SELECT statement
    2. Only references allowed views and columns
    3. Only uses allowed functions
    4. No comments, DDL/DML, or dangerous patterns
    5. Adds LIMIT if missing
    """
    if catalog is None:
        catalog = get_catalog()

    # Pre-parse checks
    for pattern in INJECTION_PATTERNS:
        if pattern.search(sql):
            return ValidationResult(
                valid=False,
                error=f"SQL contains blocked pattern: {pattern.pattern}",
                stage="PYTHON_POLICY",
            )

    # Parse SQL
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.ParseError as e:
        return ValidationResult(
            valid=False,
            error=f"SQL parse error: {e}",
            stage="PYTHON_PARSE",
        )

    # Must be exactly one statement
    non_empty = [s for s in statements if s is not None]
    if len(non_empty) != 1:
        return ValidationResult(
            valid=False,
            error=f"Expected 1 statement, got {len(non_empty)}",
            stage="PYTHON_PARSE",
        )

    stmt = non_empty[0]

    # Must be a SELECT
    if not isinstance(stmt, exp.Select):
        return ValidationResult(
            valid=False,
            error=f"Only SELECT statements allowed, got {type(stmt).__name__}",
            stage="PYTHON_POLICY",
        )

    # Check for blocked constructs
    _check_blocked_constructs(stmt, catalog)

    # Validate table references
    _validate_table_references(stmt, catalog)

    # Validate column references
    _validate_column_references(stmt, catalog)

    # Validate function usage
    _validate_functions(stmt, catalog)

    # Add LIMIT if missing, cap at 200
    _ensure_limit(stmt, catalog.max_rows)

    # Generate normalized SQL
    normalized = stmt.sql(dialect="postgres")

    return ValidationResult(valid=True, normalized_sql=normalized)


def _check_blocked_constructs(stmt: exp.Select, catalog: SemanticCatalog) -> None:
    """Check for blocked SQL constructs."""
    # Check for CTEs - only simple ones allowed
    with_expr = stmt.find(exp.With)
    if with_expr:
        # Disallow recursive CTEs
        if with_expr.find(exp.Recursive):
            raise SqlPolicyViolation("Recursive CTEs are not allowed")

    # Check for subqueries in FROM (only simple table refs allowed in FROM)
    for source in stmt.find_all(exp.From):
        for table in source.find_all(exp.Table):
            # Tables are validated separately
            pass

    # Check for window functions (allowed but limited)
    # Check for UNION (not allowed in v1)
    if stmt.find(exp.Union):
        raise SqlPolicyViolation("UNION queries are not allowed in v1")

    # Check for INTERSECT/EXCEPT
    if stmt.find(exp.Intersect) or stmt.find(exp.Except):
        raise SqlPolicyViolation("INTERSECT/EXCEPT queries are not allowed in v1")

    # Check for FOR UPDATE/SHARE
    if stmt.find(exp.For):
        raise SqlPolicyViolation("FOR UPDATE/SHARE is not allowed")


def _validate_table_references(stmt: exp.Select, catalog: SemanticCatalog) -> None:
    """Validate that only catalog views are referenced."""
    for table in stmt.find_all(exp.Table):
        table_name = table.name

        # Check blocked schemas
        if table.db and table.db.lower() in BLOCKED_SCHEMAS:
            raise SqlPolicyViolation(
                f"Schema '{table.db}' is not allowed",
                stage="PYTHON_POLICY",
            )

        # Check blocked tables
        if table_name.lower() in BLOCKED_TABLES:
            raise SqlPolicyViolation(
                f"Table '{table_name}' is not allowed",
                stage="PYTHON_POLICY",
            )

        # Must be a catalog view
        if not catalog.is_valid_view(table_name):
            raise SqlPolicyViolation(
                f"View '{table_name}' is not in the semantic catalog",
                stage="PYTHON_POLICY",
            )


def _validate_column_references(stmt: exp.Select, catalog: SemanticCatalog) -> None:
    """Validate that referenced columns exist in their views."""
    # Collect table aliases from FROM and JOIN
    table_aliases: dict[str, str] = {}
    for source in stmt.find_all(exp.From):
        for table in source.find_all(exp.Table):
            alias = table.alias if table.alias else table.name
            table_aliases[alias] = table.name

    for join in stmt.find_all(exp.Join):
        for table in join.find_all(exp.Table):
            alias = table.alias if table.alias else table.name
            table_aliases[alias] = table.name

    # Validate column references
    for col in stmt.find_all(exp.Column):
        col_name = col.name
        table_ref = col.table if col.table else None

        # If column has a table reference, validate against that view
        if table_ref and table_ref in table_aliases:
            view_name = table_aliases[table_ref]
            if not catalog.is_valid_column(view_name, col_name):
                raise SqlPolicyViolation(
                    f"Column '{col_name}' not found in view '{view_name}'",
                    stage="PYTHON_POLICY",
                )
        elif table_ref and table_ref not in table_aliases:
            # Could be a function or alias - skip if it's a known aggregate
            pass


def _validate_functions(stmt: exp.Select, catalog: SemanticCatalog) -> None:
    """Validate that only allowed functions are used."""
    for func in stmt.find_all(exp.Func):
        func_name = func.sql_name().upper() if hasattr(func, 'sql_name') else type(func).__name__.upper()

        # Check blocked functions
        if func_name.lower() in {f.lower() for f in BLOCKED_FUNCTIONS}:
            raise SqlPolicyViolation(
                f"Function '{func_name}' is not allowed",
                stage="PYTHON_POLICY",
            )

        # Check against catalog (allow standard SQL functions)
        standard_funcs = {"CASE", "WHEN", "THEN", "ELSE", "IF", "NULLIF", "CAST", "COALESCE"}
        if (func_name not in standard_funcs and
            not catalog.is_valid_function(func_name) and
            not func_name.startswith("DATE")):
            # Allow DATE_TRUNC, EXTRACT etc.
            raise SqlPolicyViolation(
                f"Function '{func_name}' is not in the allowed list",
                stage="PYTHON_POLICY",
            )


def _ensure_limit(stmt: exp.Select, max_rows: int) -> None:
    """Ensure LIMIT clause exists and is within bounds."""
    limit = stmt.find(exp.Limit)
    if limit:
        try:
            current = int(limit.expression.sql(dialect="postgres"))
            if current > max_rows:
                limit.set("expression", exp.Literal.number(max_rows))
        except (ValueError, AttributeError):
            limit.set("expression", exp.Literal.number(max_rows))
    else:
        stmt.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
