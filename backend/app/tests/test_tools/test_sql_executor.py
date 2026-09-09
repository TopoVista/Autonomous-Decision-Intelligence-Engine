from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.tools.sql_executor import SQLExecutor


@pytest.mark.asyncio
async def test_sql_executor_returns_rows(tmp_path: Path):
    db_path = tmp_path / "executor.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, revenue REAL)")
    conn.executemany("INSERT INTO orders (revenue) VALUES (?)", [(100.0,), (150.5,), (90.0,)])
    conn.commit()
    conn.close()

    executor = SQLExecutor()
    result = await executor.execute(f"sqlite+aiosqlite:///{db_path}", "SELECT revenue FROM orders ORDER BY id")
    assert result["success"] is True
    assert result["row_count"] == 3
    assert result["rows"][0]["revenue"] == 100.0


@pytest.mark.asyncio
async def test_sql_executor_blocks_non_select(tmp_path: Path):
    db_path = tmp_path / "executor.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, revenue REAL)")
    conn.commit()
    conn.close()

    executor = SQLExecutor()
    result = await executor.execute(f"sqlite+aiosqlite:///{db_path}", "DELETE FROM orders")
    assert result["success"] is False
    assert "Only SELECT" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql",
    [
        "WITH removed AS (DELETE FROM orders RETURNING id) SELECT * FROM removed",
        "SELECT pg_sleep(1)",
    ],
)
async def test_sql_executor_blocks_mutating_ctes_and_risky_functions(tmp_path: Path, sql: str):
    db_path = tmp_path / "executor.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, revenue REAL)")
    conn.commit()
    conn.close()

    result = await SQLExecutor().execute(f"sqlite+aiosqlite:///{db_path}", sql)
    assert result["success"] is False


@pytest.mark.asyncio
async def test_sql_executor_enforces_configured_result_cap(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "limited.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE values_table (value INTEGER)")
    conn.executemany("INSERT INTO values_table (value) VALUES (?)", [(i,) for i in range(10)])
    conn.commit()
    conn.close()

    monkeypatch.setenv("MAX_QUERY_ROWS", "3")
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        result = await SQLExecutor().execute(f"sqlite+aiosqlite:///{db_path}", "SELECT value FROM values_table LIMIT 10")
        assert result["success"] is True
        assert result["row_count"] == 3
    finally:
        get_settings.cache_clear()
