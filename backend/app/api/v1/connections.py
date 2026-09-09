from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_current_user, get_db
from app.schemas.auth import AuthenticatedUser
from app.schemas.connection import ConnectionCreate, ConnectionRead, ConnectionTestResponse
from app.services.connection_service import ConnectionService
from app.services.user_service import ensure_user

router = APIRouter(prefix="/connections", tags=["connections"])


@router.get("", response_model=list[ConnectionRead])
async def list_connections(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db=Depends(get_db),
):
    user = await ensure_user(db, current_user)
    service = ConnectionService(db)
    connections = await service.list_connections(user.id)
    return connections


@router.post("", response_model=ConnectionRead)
async def create_connection(
    request: ConnectionCreate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db=Depends(get_db),
):
    user = await ensure_user(db, current_user)
    service = ConnectionService(db)
    try:
        conn = await service.create_connection(user.id, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create the connection.",
        ) from exc
    return conn


@router.post("/{connection_id}/test", response_model=ConnectionTestResponse)
async def test_connection(
    connection_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db=Depends(get_db),
):
    user = await ensure_user(db, current_user)
    service = ConnectionService(db)
    try:
        result = await service.test_connection(connection_id, user.id)
        if not result["success"] and result["message"] == "Connection not found":
            raise HTTPException(status_code=404, detail="Connection not found")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to test the connection.",
        ) from exc


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db=Depends(get_db),
):
    user = await ensure_user(db, current_user)
    service = ConnectionService(db)
    deleted = await service.delete_connection(connection_id, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Connection not found")
