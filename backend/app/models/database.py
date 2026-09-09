from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> dict:
    settings = get_settings()
    kwargs: dict = {"future": True, "echo": settings.environment == "development"}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(
            {
                "pool_pre_ping": True,
                "pool_size": settings.database_pool_size,
                "max_overflow": settings.database_max_overflow,
                "pool_timeout": settings.database_pool_timeout,
                "pool_recycle": settings.database_pool_recycle_seconds,
                "connect_args": {"timeout": settings.database_connect_timeout_seconds},
            }
        )
    return kwargs


@lru_cache
def get_engine():
    settings = get_settings()
    return create_async_engine(settings.database_url, **_engine_kwargs(settings.database_url))


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async_session = get_sessionmaker()
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def dispose_engine() -> None:
    """Release pooled connections during a graceful Render shutdown."""
    engine = get_engine()
    await engine.dispose()
    get_sessionmaker.cache_clear()
    get_engine.cache_clear()
