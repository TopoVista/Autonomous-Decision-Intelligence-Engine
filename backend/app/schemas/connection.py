from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    db_type: str = Field(default="postgresql", min_length=1, max_length=50)
    host: str = Field(min_length=1, max_length=500)
    port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    ssl_mode: str = Field(default="require", pattern="^(require|prefer|disable)$")


class ConnectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    name: str
    db_type: str
    host: str
    port: int
    database_name: str
    username: str
    ssl_mode: str
    is_active: bool
    last_tested_at: datetime | None = None
    created_at: datetime


class ConnectionTestResponse(BaseModel):
    success: bool
    message: str
