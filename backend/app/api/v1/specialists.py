"""API endpoints for specialist listing and invocation."""

from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
import structlog
from pydantic import BaseModel, Field

from app.dependencies import get_current_user, get_db
from app.schemas.auth import AuthenticatedUser
from app.services.user_service import ensure_user

router = APIRouter(prefix="/specialists", tags=["specialists"])
logger = structlog.get_logger()


class InvokeRequest(BaseModel):
    skill: str = Field(min_length=1, max_length=100)
    params: dict[str, Any] = Field(default_factory=dict)


def _registered_skills(instance: Any) -> dict[str, Any]:
    return {
        getattr(method, "__skill_name__"): method
        for _, method in inspect.getmembers(instance, predicate=callable)
        if getattr(method, "__skill_name__", None)
    }


@router.get("/")
async def list_specialists(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """List all registered specialists with their capabilities."""
    await ensure_user(db, current_user)

    from app.specialists import get_specialist_class, specialist_registry

    specialists = specialist_registry.list(available_only=False)
    return {
        "specialists": [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "capabilities": s.capabilities,
                "supported_data_types": s.supported_data_types,
                "tools": s.tools,
                "available": s.available,
                "direct_invocation": get_specialist_class(s.id) is not None,
            }
            for s in specialists
        ],
        "count": len(specialists),
    }


@router.get("/{specialist_id}")
async def get_specialist(
    specialist_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get details of a specific specialist."""
    await ensure_user(db, current_user)

    from app.specialists import get_specialist_class, specialist_registry

    spec = specialist_registry.metadata(specialist_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Specialist '{specialist_id}' not found")

    return {
        "id": spec.id,
        "name": spec.name,
        "description": spec.description,
        "capabilities": spec.capabilities,
        "supported_data_types": spec.supported_data_types,
        "tools": spec.tools,
        "available": spec.available,
        "direct_invocation": get_specialist_class(spec.id) is not None,
    }


@router.post("/{specialist_id}/invoke")
async def invoke_specialist(
    specialist_id: str,
    body: InvokeRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Invoke a specific skill on a specialist directly.

    Example request body::

        {
            "skill": "sentiment",
            "params": {"text": "This product is amazing!"}
        }

    Returns the raw skill output as JSON. All specialist skills are pure Python
    and execute in-process — no external API calls required.
    """
    await ensure_user(db, current_user)

    from app.specialists import specialist_registry, get_specialist_class

    spec = specialist_registry.metadata(specialist_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Specialist '{specialist_id}' not found")

    if not spec.available:
        raise HTTPException(
            status_code=503,
            detail=f"Specialist '{specialist_id}' is not currently available.",
        )

    cls = get_specialist_class(specialist_id)
    if cls is None:
        raise HTTPException(
            status_code=501,
            detail=f"Specialist '{specialist_id}' has no invokable implementation (pipeline-only).",
        )

    # Only explicitly decorated skills are public. Never allow a client to
    # call an arbitrary public method (for example register or helper methods).
    instance = cls()
    registered_skills = _registered_skills(instance)
    skill_method = registered_skills.get(body.skill)

    if skill_method is None:
        # List available skills
        raise HTTPException(
            status_code=404,
            detail=f"Skill '{body.skill}' not found on specialist '{specialist_id}'. "
            f"Available: {sorted(registered_skills)}",
        )

    try:
        inspect.signature(skill_method).bind(**body.params)
        result = await skill_method(**body.params)
    except TypeError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid parameters for skill '{body.skill}': {exc}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid parameters for skill '{body.skill}': {exc}",
        ) from exc
    except Exception as exc:
        logger.exception("specialist_skill_failed", specialist_id=specialist_id, skill=body.skill)
        raise HTTPException(
            status_code=500,
            detail="Specialist execution failed. Please retry with valid input.",
        ) from exc

    return {
        "specialist_id": specialist_id,
        "skill": body.skill,
        "result": result,
    }
