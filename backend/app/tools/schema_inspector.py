from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import MetaData, Table, inspect
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings


class SchemaInspector:
    async def get_schema(self, connection_string: str) -> dict[str, Any]:
        engine = create_async_engine(connection_string, future=True, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                return await asyncio.wait_for(conn.run_sync(self._get_schema_sync), timeout=get_settings().schema_timeout_seconds)
        finally:
            await engine.dispose()

    def _get_schema_sync(self, sync_conn) -> dict[str, Any]:
        inspector = inspect(sync_conn)
        metadata = MetaData()
        schema: dict[str, Any] = {"tables": {}}
        table_names = inspector.get_table_names(schema=None)
        limited_tables = table_names[:get_settings().schema_max_tables]
        for table_name in limited_tables:
            table = Table(table_name, metadata, autoload_with=sync_conn)
            columns = []
            for column in table.columns:
                columns.append(
                    {
                        "name": column.name,
                        "type": str(column.type),
                        "nullable": bool(column.nullable),
                        "default": str(column.default.arg) if column.default is not None and getattr(column.default, "arg", None) is not None else None,
                    }
                )
            foreign_keys = []
            for fk in inspector.get_foreign_keys(table_name):
                target_table = fk.get("referred_table")
                referred_columns = fk.get("referred_columns") or []
                local_columns = fk.get("constrained_columns") or []
                if local_columns and target_table and referred_columns:
                    foreign_keys.append(
                        {
                            "column": local_columns[0],
                            "references": f"{target_table}.{referred_columns[0]}",
                        }
                    )
            schema["tables"][table_name] = {
                "columns": columns,
                "primary_keys": inspector.get_pk_constraint(table_name).get("constrained_columns", []),
                "foreign_keys": foreign_keys,
                "indexes": [index.get("name") for index in inspector.get_indexes(table_name)],
                "row_count_estimate": None,
                # Schema inspection deliberately does not read table values:
                # a user can validate structure without copying PII into the
                # application database/cache or an LLM prompt.
                "sample_rows": [],
            }
        schema["truncated"] = len(table_names) > len(limited_tables)
        return schema

    def to_prompt_string(self, schema: dict) -> str:
        lines = ["DATABASE SCHEMA:"]
        max_chars = get_settings().max_schema_prompt_chars
        for table_name, table_info in schema.get("tables", {}).items():
            lines.append(f"\n{table_name} (~{table_info.get('row_count_estimate', '?')} rows)")
            cols = ", ".join(
                f"{col['name']} ({col['type']}{'?' if col['nullable'] else ''})"
                for col in table_info.get("columns", [])[:100]
            )
            lines.append(f"  Columns: {cols}")
            if table_info.get("foreign_keys"):
                fk_text = ", ".join(
                    f"{fk['column']}→{fk['references']}" for fk in table_info["foreign_keys"]
                )
                lines.append(f"  FK: {fk_text}")
            if len("\n".join(lines)) >= max_chars:
                lines.append("\n[schema prompt truncated]")
                break
        if schema.get("truncated"):
            lines.append("\n[schema table list truncated]")
        return "\n".join(lines)[:max_chars]
