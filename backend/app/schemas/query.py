from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)
    connection_id: str
    session_id: str | None = None


class QueryHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    user_id: UUID
    user_question: str
    intent_type: str | None = None
    task_plan: dict | None = None
    generated_queries: list | None = None
    analysis_result: dict | None = None
    hypotheses: list | None = None
    final_insight: str | None = None
    anomalies_detected: list | None = None
    execution_time_ms: int | None = None
    total_tokens_used: int | None = None
    error: str | None = None
    created_at: datetime
