"""First-class output artifacts.

Every analysis product (chart, table, insight, dashboard, dataset profile...)
is persisted as an Artifact so results stay inspectable and composable.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

ARTIFACT_TYPES = frozenset(
    {
        "analysis",
        "chart",
        "table",
        "dashboard",
        "report",
        "dataset",
        "profile",
        "model",
        "sql_query",
        "code",
        "insight",
        "document",
    }
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Artifact:
    id: str
    type: str
    created_at: str
    session_id: str | None = None
    source: str = ""
    dependencies: list[str] = field(default_factory=list)
    content: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ArtifactStore:
    """In-memory artifact store keyed by session. Swappable later."""

    _by_session: dict[str, list[Artifact]] = field(default_factory=dict, repr=False)

    def create(
        self,
        type: str,
        content: Any,
        *,
        session_id: str | None = None,
        source: str = "",
        dependencies: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        if type not in ARTIFACT_TYPES:
            raise ValueError(f"unknown artifact type '{type}'")
        artifact = Artifact(
            id=uuid.uuid4().hex,
            type=type,
            created_at=_utcnow().isoformat(),
            session_id=session_id,
            source=source,
            dependencies=list(dependencies or []),
            content=content,
            metadata=dict(metadata or {}),
        )
        key = session_id or "_global"
        items = self._by_session.setdefault(key, [])
        items.append(artifact)
        from app.config import get_settings
        overflow = len(items) - get_settings().max_artifacts_per_session
        if overflow > 0:
            del items[:overflow]
        return artifact

    def list(self, session_id: str | None = None, *, type: str | None = None) -> list[Artifact]:
        items = self._by_session.get(session_id or "_global", [])
        if type is not None:
            items = [a for a in items if a.type == type]
        return list(items)

    def get(self, artifact_id: str) -> Artifact | None:
        for items in self._by_session.values():
            for artifact in items:
                if artifact.id == artifact_id:
                    return artifact
        return None


# Shared default store; API layer can also construct scoped stores.
default_artifact_store = ArtifactStore()
