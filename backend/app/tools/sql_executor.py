from __future__ import annotations

import asyncio
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.tools.sql_validator import validate_sql


class SQLExecutor:
    async def execute(self, connection_string: str, sql: str, timeout: float = 30.0) -> dict[str, Any]:
        start = time.monotonic()
        settings = get_settings()
        validation = validate_sql(sql)
        if not validation.is_valid:
            return {
                "success": False,
                "rows": [],
                "columns": [],
                "row_count": 0,
                "error": validation.reason,
                "execution_time_ms": int((time.monotonic() - start) * 1000),
            }

        normalized_sql = validation.normalized_sql
        # An outer limit is deliberate: an LLM-provided LIMIT can otherwise be
        # arbitrarily large.  It also bounds the rows materialised for JSON.
        normalized_sql = f"SELECT * FROM ({normalized_sql}) AS bounded_query LIMIT {settings.max_query_rows}"

        try:
            # User connections are dynamic.  A persistent pool per URL would
            # grow without bound, so release each connection deterministically.
            engine = create_async_engine(connection_string, future=True, poolclass=NullPool)
        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            return {
                "success": False,
                "rows": [],
                "columns": [],
                "row_count": 0,
                "error": f"Failed to create database engine: {str(exc)}",
                "execution_time_ms": elapsed,
            }

        try:
            async with engine.connect() as conn:
                if connection_string.startswith("postgresql"):
                    await conn.execute(
                        text("SELECT set_config('statement_timeout', :timeout_ms, true)"),
                        {"timeout_ms": str(int(timeout * 1000))},
                    )
                result = await asyncio.wait_for(conn.execute(text(normalized_sql)), timeout=timeout)
                rows = [dict(row._mapping) for row in result.fetchall()] if result.returns_rows else []
                columns = list(result.keys()) if result.returns_rows else []
                elapsed = int((time.monotonic() - start) * 1000)
                return {
                    "success": True,
                    "rows": rows,
                    "columns": columns,
                    "row_count": len(rows),
                    "error": None,
                    "execution_time_ms": elapsed,
                }
        except asyncio.TimeoutError:
            elapsed = int((time.monotonic() - start) * 1000)
            return {
                "success": False, "rows": [], "columns": [], "row_count": 0,
                "error": "Query timed out. Try a narrower question or a smaller date range.",
                "execution_time_ms": elapsed,
            }
        except SQLAlchemyError as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            return {
                "success": False,
                "rows": [],
                "columns": [],
                "row_count": 0,
                "error": str(exc),
                "execution_time_ms": elapsed,
            }
        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            return {
                "success": False,
                "rows": [],
                "columns": [],
                "row_count": 0,
                "error": f"Unexpected error: {str(exc)}",
                "execution_time_ms": elapsed,
            }
        finally:
            await engine.dispose()
