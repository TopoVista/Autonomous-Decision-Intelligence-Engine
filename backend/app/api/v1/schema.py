from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app.config import get_settings
from app.dependencies import get_current_user, get_db
from app.models.schema_cache import SchemaCache
from app.schemas.auth import AuthenticatedUser
from app.services.connection_service import ConnectionService
from app.services.user_service import ensure_user
from app.tools.schema_inspector import SchemaInspector

router = APIRouter(prefix="/schema", tags=["schema"])
settings = get_settings()


@router.get("/{connection_id}")
async def get_schema(
    connection_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db=Depends(get_db),
):
    user = await ensure_user(db, current_user)
    conn_service = ConnectionService(db)
    connection_string = await conn_service.get_decrypted_connection_string(connection_id, user.id)
    if not connection_string:
        raise HTTPException(status_code=404, detail="Connection not found")

    connection_uuid = UUID(str(connection_id))
    result = await db.execute(select(SchemaCache).where(SchemaCache.connection_id == connection_uuid))
    cached = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if cached and cached.expires_at and cached.expires_at > now:
        return {"schema": cached.schema_json, "cached": True, "prompt_string": SchemaInspector().to_prompt_string(cached.schema_json)}

    inspector = SchemaInspector()
    try:
        schema = await inspector.get_schema(connection_string)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Unable to inspect schema for this connection") from exc
    schema = jsonable_encoder(schema)
    prompt_string = inspector.to_prompt_string(schema)
    if cached is None:
        cached = SchemaCache(
            connection_id=connection_uuid,
            schema_json=schema,
            table_count=len(schema.get("tables", {})),
            expires_at=now + timedelta(seconds=settings.cache_ttl_schema),
        )
        db.add(cached)
    else:
        cached.schema_json = schema
        cached.table_count = len(schema.get("tables", {}))
        cached.expires_at = now + timedelta(seconds=settings.cache_ttl_schema)
    await db.flush()
    return {"schema": schema, "cached": False, "prompt_string": prompt_string}
