from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.tools.sql_validator import validate_sql


_query_semaphore: asyncio.Semaphore | None = None


def _get_query_semaphore() -> asyncio.Semaphore:
    global _query_semaphore
    if _query_semaphore is None:
        _query_semaphore = asyncio.Semaphore(get_settings().max_concurrent_db_queries)
    return _query_semaphore


def _failure(start: float, message: str) -> dict[str, Any]:
    return {
        "success": False, "rows": [], "columns": [], "row_count": 0,
        "error": message, "execution_time_ms": int((time.monotonic() - start) * 1000),
    }


class SQLExecutor:
    async def execute(self, connection_string: str, sql: str, timeout: float | None = None) -> dict[str, Any]:
        start = time.monotonic()
        settings = get_settings()
        timeout = timeout or settings.query_timeout_seconds
        validation = validate_sql(sql)
        if not validation.is_valid:
            return _failure(start, validation.reason)

        normalized_sql = validation.normalized_sql
        # An outer limit is deliberate: an LLM-provided LIMIT can otherwise be
        # arbitrarily large.  It also bounds the rows materialised for JSON.
        normalized_sql = f"SELECT * FROM ({normalized_sql}) AS bounded_query LIMIT {settings.max_query_rows}"

        engine = None
        try:
            # User connections are dynamic.  A persistent pool per URL would
            # grow without bound, so release each connection deterministically.
            connect_args = {"timeout": settings.database_connect_timeout_seconds} if connection_string.startswith("postgresql") else {}
            engine = create_async_engine(connection_string, future=True, poolclass=NullPool, connect_args=connect_args)
        except Exception:
            return _failure(start, "Unable to create a connection to this database.")

        try:
            async def _run_query() -> tuple[list[dict[str, Any]], list[str]]:
                async with engine.connect() as conn:
                    if connection_string.startswith("postgresql"):
                        await conn.execute(
                            text("SELECT set_config('statement_timeout', :timeout_ms, true)"),
                            {"timeout_ms": str(int(timeout * 1000))},
                        )
                    result = await conn.execute(text(normalized_sql))
                    if not result.returns_rows:
                        return [], []
                    columns = list(result.keys())
                    rows: list[dict[str, Any]] = []
                    result_bytes = 0
                    for row in result.mappings().fetchmany(settings.max_query_rows):
                        mapped = dict(row)
                        result_bytes += len(json.dumps(mapped, default=str, ensure_ascii=False).encode("utf-8"))
                        if result_bytes > settings.max_query_result_bytes:
                            raise ValueError("Query result is too large to return safely. Add filters or aggregate the data.")
                        rows.append(mapped)
                    return rows, columns

            async with _get_query_semaphore():
                rows, columns = await asyncio.wait_for(_run_query(), timeout=timeout)
            return {
                "success": True, "rows": rows, "columns": columns, "row_count": len(rows),
                "error": None, "execution_time_ms": int((time.monotonic() - start) * 1000),
            }
        except asyncio.TimeoutError:
            return _failure(start, "Query timed out. Try a narrower question or a smaller date range.")
        except ValueError as exc:
            return _failure(start, str(exc))
        except SQLAlchemyError:
            return _failure(start, "Database rejected the query or connection. Check permissions and the query.")
        except Exception:
            return _failure(start, "Database query failed. Check the query, permissions, and connection.")
        finally:
            if engine is not None:
                await engine.dispose()
