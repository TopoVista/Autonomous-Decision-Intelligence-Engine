"""File ingestion: CSV/TSV/JSON -> per-dataset SQLite table.

Uses only the stdlib (csv, json, io) + SQLAlchemy (already a dep) so we
stay well within the 512 MB Render free-tier memory budget.  Excel and
Parquet are accepted at the API level but rejected gracefully (users are
told to export to CSV first).
"""

from __future__ import annotations

import csv
import io
import json
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.data.descriptor import DatasetDescriptor
from app.data.profiler import fast_profile

SUPPORTED_EXTENSIONS = {".csv", ".tsv", ".json"}
_HEAVY_EXTENSIONS = {".xlsx", ".xls", ".parquet"}
# Lists of Python dicts are substantially larger than their source CSV.  This
# keeps profiling and persistence inside the memory budget of a small instance.
MAX_ROWS = 100_000


class IngestionError(ValueError):
    pass


def detect_source_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in _HEAVY_EXTENSIONS:
        raise IngestionError(
            f"'{ext}' files are not supported on the free tier. "
            "Please export your data as CSV and re-upload."
        )
    if ext not in SUPPORTED_EXTENSIONS:
        raise IngestionError(
            f"unsupported file type '{ext}'. Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )
    return ext.lstrip(".")


# ── Pure-Python loaders ──────────────────────────────────────────────────────

def _load_csv(content: bytes, delimiter: str = ",") -> tuple[list[str], list[dict]]:
    text_io = io.StringIO(content.decode("utf-8-sig", errors="replace"))
    reader = csv.DictReader(text_io, delimiter=delimiter)
    rows = list(reader)
    columns = list(reader.fieldnames or [])
    return columns, rows


def _load_json(content: bytes) -> tuple[list[str], list[dict]]:
    data = json.loads(content)
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        # Try records-oriented dict (e.g. pandas to_json orient='split')
        if "data" in data and "columns" in data:
            cols = data["columns"]
            rows = [dict(zip(cols, r)) for r in data["data"]]
            return cols, rows
        rows = [data]
    else:
        raise IngestionError("JSON must be an array of objects or a records dict")
    if not rows or not isinstance(rows[0], dict):
        raise IngestionError("JSON must contain an array of objects")
    columns = list(rows[0].keys())
    return columns, rows


def load_table(filename: str, content: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    ext = Path(filename).suffix.lower()
    try:
        if ext == ".csv":
            cols, rows = _load_csv(content, ",")
        elif ext == ".tsv":
            cols, rows = _load_csv(content, "\t")
        elif ext == ".json":
            cols, rows = _load_json(content)
        else:
            raise IngestionError(f"unsupported extension {ext}")
    except IngestionError:
        raise
    except Exception as exc:
        raise IngestionError(f"could not parse '{filename}': {exc}") from exc

    if not rows:
        raise IngestionError(f"'{filename}' contains no rows")
    if len(rows) > MAX_ROWS:
        raise IngestionError(f"'{filename}' has {len(rows)} rows; limit is {MAX_ROWS}")

    # Normalize column names
    seen: dict[str, int] = {}
    clean_cols: list[str] = []
    for i, c in enumerate(cols):
        base = (str(c).strip().replace(" ", "_").lower()[:60] or f"col_{i}")
        base = re.sub(r"[^a-z0-9_]", "_", base)
        if base in seen:
            seen[base] += 1
            base = f"{base}_{seen[base]}"
        seen[base] = 0
        clean_cols.append(base)

    if clean_cols != cols:
        rows = [{new: row.get(old) for new, old in zip(clean_cols, cols)} for row in rows]

    return clean_cols, rows


# ── SQLite persistence (sync via SQLAlchemy core) ───────────────────────────

def _sanitize_table_name(name: str) -> str:
    clean = re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_")
    return clean or "data"


def persist_dataset(
    columns: list[str],
    rows: list[dict],
    uploads_dir: Path,
    dataset_id: str,
    table: str = "data",
) -> Path:
    from sqlalchemy import create_engine, Column, Table, MetaData, String

    uploads_dir.mkdir(parents=True, exist_ok=True)
    db_path = uploads_dir / f"{dataset_id}.db"
    engine = create_engine(f"sqlite:///{db_path}")

    # Infer column types from first non-null values
    col_types: list[str] = []
    for c in columns:
        for row in rows:
            v = row.get(c)
            if v is None or v == "":
                continue
            try:
                int(str(v))
                col_types.append("INTEGER")
            except ValueError:
                try:
                    float(str(v))
                    col_types.append("REAL")
                except ValueError:
                    col_types.append("TEXT")
            break
        else:
            col_types.append("TEXT")

    col_defs = ", ".join(f'"{c}" {t}' for c, t in zip(columns, col_types))
    placeholders = ", ".join(f":{c}" for c in columns)

    with engine.begin() as conn:
        conn.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
        conn.execute(text(f'CREATE TABLE "{table}" ({col_defs})'))
        if rows:
            conn.execute(text(f'INSERT INTO "{table}" ({", ".join(f"{chr(34)}{c}{chr(34)}" for c in columns)}) VALUES ({placeholders})'), rows)

    engine.dispose()
    return db_path


def ingest_bytes(
    filename: str,
    content: bytes,
    *,
    dataset_id: str | None = None,
    uploads_dir: Path,
) -> tuple[DatasetDescriptor, Path, list[dict]]:
    dataset_id = dataset_id or uuid.uuid4().hex
    detect_source_type(filename)
    columns, rows = load_table(filename, content)
    table = _sanitize_table_name(Path(filename).stem.lower()) or "data"
    db_path = persist_dataset(columns, rows, uploads_dir, dataset_id, table=table)
    descriptor = fast_profile(
        columns,
        rows,
        descriptor_id=dataset_id,
        name=Path(filename).stem,
        source=f"file:{Path(filename).suffix.lstrip('.')}",
        table_name=table,
    )
    descriptor.size_bytes = len(content)
    return descriptor, db_path, rows


def read_table(db_path: Path, table: str, limit: int | None = None) -> list[dict]:
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        query = f'SELECT * FROM "{table}"'
        if limit:
            query += f" LIMIT {int(limit)}"
        with engine.connect() as conn:
            result = conn.execute(text(query))
            keys = list(result.keys())
            return [dict(zip(keys, row)) for row in result]
    finally:
        engine.dispose()


def table_summary(columns: list[str], rows: list[dict], limit: int = 5) -> dict[str, Any]:
    return {
        "columns": columns,
        "rows": rows[:limit],
        "total_rows": len(rows),
        "preview_limit": limit,
    }
