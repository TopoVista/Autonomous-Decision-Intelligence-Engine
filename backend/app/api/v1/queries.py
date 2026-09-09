from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from app.agents.pipeline import AgentPipeline
from app.dependencies import get_current_user, get_db
from app.models.database import get_sessionmaker
from app.models.query_history import QueryHistory
from app.models.session import QuerySession
from app.schemas.auth import AuthenticatedUser
from app.schemas.query import QueryRequest
from app.services.connection_service import ConnectionService
from app.config import get_settings
from app.services.user_service import ensure_user

router = APIRouter(prefix="/queries", tags=["queries"])
_agent_semaphore: asyncio.Semaphore | None = None


def _get_agent_semaphore() -> asyncio.Semaphore:
    global _agent_semaphore
    if _agent_semaphore is None:
        _agent_semaphore = asyncio.Semaphore(get_settings().max_concurrent_agent_runs)
    return _agent_semaphore
# sessionmaker is obtained lazily (inside route handlers) so the DB engine
# is created after all env-vars / Settings validators have run.


@router.post("/run")
async def run_query(
    request: QueryRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db=Depends(get_db),
):
    user = await ensure_user(db, current_user)
    conn_service = ConnectionService(db)
    connection_string = await conn_service.get_decrypted_connection_string(request.connection_id, user.id)
    if not connection_string:
        raise HTTPException(status_code=404, detail="Connection not found")

    session_id = request.session_id or str(uuid4())
    try:
        connection_uuid = UUID(str(request.connection_id))
        session_uuid = UUID(str(session_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="connection_id and session_id must be UUIDs") from exc
    existing_session = await db.get(QuerySession, session_uuid)
    if existing_session is None:
        db.add(QuerySession(id=session_uuid, user_id=user.id, connection_id=connection_uuid, title=request.question[:120]))
        await db.flush()

    pipeline = AgentPipeline()
    sessionmaker = get_sessionmaker()

    async def event_generator():
        final_data = None
        error_message = None
        try:
            async with _get_agent_semaphore():
                async with asyncio.timeout(get_settings().agent_timeout_seconds):
                    async for event in pipeline.run(
                        user_question=request.question,
                        connection_string=connection_string,
                        session_id=session_id,
                        user_id=str(user.id),
                        connection_id=request.connection_id,
                    ):
                        if event["type"] == "done":
                            final_data = event["data"]
                        yield f"data: {json.dumps(event, default=str)}\n\n"
            if final_data is not None:
                async with sessionmaker() as history_db:
                    history = QueryHistory(
                        session_id=session_uuid,
                        user_id=user.id,
                        user_question=request.question,
                        intent_type=final_data.get("intent", {}).get("intent") if isinstance(final_data.get("intent"), dict) else None,
                        task_plan=final_data.get("plan"),
                        generated_queries=final_data.get("query_results"),
                        analysis_result=final_data.get("analysis"),
                        hypotheses=final_data.get("hypotheses"),
                        final_insight=final_data.get("final_insight"),
                        anomalies_detected=final_data.get("analysis", {}).get("statistical_anomalies", []),
                        execution_time_ms=final_data.get("execution_time_ms"),
                        total_tokens_used=len(final_data.get("final_insight", "").split()),
                        error=None,
                    )
                    history_db.add(history)
                    await history_db.commit()
        except TimeoutError:
            error_message = "Analysis timed out. Try a narrower question or a smaller date range."
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': error_message}})}\n\n"
        except Exception as exc:
            error_message = ConnectionService.describe_connection_error(str(exc))
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': error_message}})}\n\n"
        if error_message is not None:
            async with sessionmaker() as history_db:
                history = QueryHistory(
                    session_id=session_uuid,
                    user_id=user.id,
                    user_question=request.question,
                    error=error_message,
                )
                history_db.add(history)
                await history_db.commit()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
