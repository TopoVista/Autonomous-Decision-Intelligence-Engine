"""Dataset service: persistence + lifecycle for ingested datasets."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.data.descriptor import DatasetDescriptor
from app.data.ingestion import IngestionError, ingest_bytes, read_table
from app.data.profiler_deep import deep_profile
from app.models.dataset import Dataset


class DatasetService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = get_settings()

    @property
    def uploads_dir(self) -> Path:
        return Path(self.settings.uploads_dir).resolve()

    async def list_datasets(self, user_id: str) -> list[Dataset]:
        result = await self.db.execute(
            select(Dataset).where(Dataset.user_id == user_id).order_by(Dataset.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_dataset(self, dataset_id: str, user_id: str) -> Dataset | None:
        result = await self.db.execute(
            select(Dataset).where(Dataset.id == dataset_id, Dataset.user_id == user_id)
        )
        return result.scalars().first()

    async def ingest_upload(self, user_id: str, filename: str, content: bytes) -> Dataset:
        if len(content) > self.settings.max_upload_bytes:
            raise IngestionError(
                f"file is {len(content)} bytes; upload limit is {self.settings.max_upload_bytes}"
            )
        dataset_id = uuid.uuid4().hex
        descriptor, db_path, _df = await asyncio.to_thread(
            ingest_bytes, filename, content, dataset_id=dataset_id, uploads_dir=self.uploads_dir
        )
        dataset = Dataset(
            id=dataset_id,
            user_id=user_id,
            name=descriptor.name,
            filename=filename,
            source_type=descriptor.source.removeprefix("file:"),
            file_path=str(db_path),
            row_count=descriptor.row_count,
            column_count=descriptor.column_count,
            descriptor_json=json.dumps(descriptor.to_dict()),
        )
        self.db.add(dataset)
        await self.db.commit()
        await self.db.refresh(dataset)
        return dataset

    async def run_deep_profile(self, dataset: Dataset) -> Dataset:
        """Run the deep profiler on an ingested dataset and persist the result."""
        descriptor = self._descriptor(dataset)
        self._require_file(dataset)
        rows = await asyncio.to_thread(read_table, Path(dataset.file_path), descriptor.table_name or "data")
        columns = [c.name for c in descriptor.columns]
        updated = await asyncio.to_thread(deep_profile, columns, rows, descriptor)
        dataset.row_count = updated.row_count
        dataset.column_count = updated.column_count
        dataset.descriptor_json = json.dumps(updated.to_dict())
        await self.db.commit()
        await self.db.refresh(dataset)
        return dataset

    def preview_table(self, dataset: Dataset, limit: int = 20) -> dict[str, Any]:
        descriptor = self._descriptor(dataset)
        self._require_file(dataset)
        rows = read_table(Path(dataset.file_path), descriptor.table_name or "data", limit=limit)
        columns = list(rows[0].keys()) if rows else [c.name for c in descriptor.columns]
        # Keep this response aligned with TablePreviewResponse.  The former
        # ``rows`` key caused a production-only response-validation 500.
        return {
            "dataset_id": dataset.id,
            "table_name": descriptor.table_name or "data",
            "columns": columns,
            "rows_preview": rows[:limit],
        }

    def connection_string(self, dataset: Dataset) -> str:
        """SQLite async connection string so the SQL agent can analyze this dataset."""
        path = Path(dataset.file_path).resolve().as_posix()
        return f"sqlite+aiosqlite:///{path}"

    def delete_dataset(self, dataset: Dataset) -> None:
        path = Path(dataset.file_path)
        if path.exists():
            path.unlink(missing_ok=True)

    def _descriptor(self, dataset: Dataset) -> DatasetDescriptor:
        data = json.loads(dataset.descriptor_json)
        return DatasetDescriptor.from_dict(data)

    @staticmethod
    def _require_file(dataset: Dataset) -> None:
        if not Path(dataset.file_path).is_file():
            raise IngestionError("Dataset working file is unavailable after a service restart. Please re-upload it.")
