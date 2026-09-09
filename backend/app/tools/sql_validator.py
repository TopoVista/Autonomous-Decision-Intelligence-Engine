from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp


@dataclass
class ValidationResult:
    is_valid: bool
    reason: str = ""
    normalized_sql: str = ""


def _strip_sql(sql: str) -> str:
    text = sql.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text.rstrip(";").strip()


def _expression_types(*names: str) -> tuple[type, ...]:
    """Resolve sqlglot node types across compatible sqlglot releases."""
    return tuple(node for name in names if isinstance((node := getattr(exp, name, None)), type))


_MUTATING_EXPRESSIONS = _expression_types(
    "Insert", "Update", "Delete", "Merge", "Create", "Alter", "Drop", "TruncateTable",
    "Grant", "Revoke", "Command", "Copy", "Transaction", "Use",
)
_BLOCKED_FUNCTIONS = {
    "pg_sleep", "dblink_connect", "dblink_exec", "lo_import", "lo_export",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "set_config",
}


def validate_sql(sql: str) -> ValidationResult:
    cleaned = _strip_sql(sql)
    if not cleaned or ";" in cleaned:
        return ValidationResult(False, "Exactly one read-only SQL statement is required.", cleaned)
    try:
        statements = sqlglot.parse(cleaned, dialect="postgres")
    except Exception:
        return ValidationResult(False, "Invalid SQL syntax.", cleaned)

    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        return ValidationResult(False, "Only SELECT or WITH queries are allowed.", cleaned)

    parsed = statements[0]
    if parsed.find(exp.Into) or any(parsed.find(kind) for kind in _MUTATING_EXPRESSIONS):
        return ValidationResult(False, "Only read-only SELECT queries are allowed.", cleaned)

    for function in parsed.find_all(exp.Func):
        name = function.sql_name().lower()
        if name in _BLOCKED_FUNCTIONS or function.name.lower() in _BLOCKED_FUNCTIONS:
            return ValidationResult(False, "This SQL function is not allowed.", cleaned)
    return ValidationResult(True, normalized_sql=cleaned)
