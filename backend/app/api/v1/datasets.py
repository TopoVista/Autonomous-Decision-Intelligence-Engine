"""Dataset upload / profiling / preview endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from app.config import get_settings
from app.data.ingestion import IngestionError, SUPPORTED_EXTENSIONS
from app.dependencies import get_current_user, get_db
from app.schemas.auth import AuthenticatedUser
from app.schemas.dataset import (
    DatasetListResponse,
    DatasetRead,
    TablePreviewResponse,
)
from app.services.dataset_service import DatasetService
from app.services.user_service import ensure_user

router = APIRouter(prefix="/datasets", tags=["datasets"])


async def _read_upload_with_limit(file: UploadFile, max_bytes: int) -> bytes:
    """Reject oversized multipart bodies before retaining the whole payload."""
    chunks: list[bytes] = []
    received = 0
    while chunk := await file.read(64 * 1024):
        received += len(chunk)
        if received > max_bytes:
            raise HTTPException(status_code=413, detail=f"Upload exceeds the {max_bytes} byte limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def _to_read(dataset, descriptor) -> DatasetRead:
    return DatasetRead(
        id=dataset.id,
        name=dataset.name,
        filename=dataset.filename,
        source_type=dataset.source_type,
        row_count=dataset.row_count,
        column_count=dataset.column_count,
        created_at=dataset.created_at,
        descriptor=descriptor,
    )


@router.get("", response_model=DatasetListResponse)
async def list_datasets(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db=Depends(get_db),
):
    user = await ensure_user(db, current_user)
    service = DatasetService(db)
    datasets = await service.list_datasets(str(user.id))
    items = [
        _to_read(d, json.loads(d.descriptor_json))
        for d in datasets
    ]
    return DatasetListResponse(datasets=items, total=len(items))


@router.post("", response_model=DatasetRead, status_code=status.HTTP_201_CREATED)
async def upload_dataset(
    file: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db=Depends(get_db),
):
    user = await ensure_user(db, current_user)
    filename = file.filename or "upload.csv"
    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported file type '{suffix}'. Supported: {sorted(SUPPORTED_EXTENSIONS)}",
        )
    content = await _read_upload_with_limit(file, get_settings().max_upload_bytes)
    service = DatasetService(db)
    try:
        dataset = await service.ingest_upload(str(user.id), filename, content)
    except IngestionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_read(dataset, json.loads(dataset.descriptor_json))


@router.get("/{dataset_id}", response_model=DatasetRead)
async def get_dataset(
    dataset_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db=Depends(get_db),
):
    user = await ensure_user(db, current_user)
    service = DatasetService(db)
    dataset = await service.get_dataset(dataset_id, str(user.id))
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return _to_read(dataset, json.loads(dataset.descriptor_json))


@router.post("/{dataset_id}/profile", response_model=DatasetRead)
async def deep_profile_dataset(
    dataset_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db=Depends(get_db),
):
    user = await ensure_user(db, current_user)
    service = DatasetService(db)
    dataset = await service.get_dataset(dataset_id, str(user.id))
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    try:
        dataset = await service.run_deep_profile(dataset)
    except IngestionError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Profiling failed. Please retry with a smaller, valid dataset.",
        ) from exc
    return _to_read(dataset, json.loads(dataset.descriptor_json))


@router.get("/{dataset_id}/preview", response_model=TablePreviewResponse)
async def preview_dataset(
    dataset_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db=Depends(get_db),
):
    user = await ensure_user(db, current_user)
    service = DatasetService(db)
    dataset = await service.get_dataset(dataset_id, str(user.id))
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    try:
        return service.preview_table(dataset, limit=limit)
    except IngestionError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(
    dataset_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db=Depends(get_db),
):
    user = await ensure_user(db, current_user)
    service = DatasetService(db)
    dataset = await service.get_dataset(dataset_id, str(user.id))
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    service.delete_dataset(dataset)
    await db.delete(dataset)
    await db.commit()
