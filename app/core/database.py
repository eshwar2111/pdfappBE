"""Database engine, session factory and the declarative base.

Connection strategy (deliberate):

    one shared AsyncEngine  ->  connection pool  ->  one Session per request

There is no global/singleton *connection* and no global session. The engine is
process-wide and owns a pool; every request checks out its own session, which is
committed or rolled back and then returned to the pool when the request ends.
That is what makes concurrent users safe.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"


engine: AsyncEngine = create_async_engine(
    str(settings.database_url),
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_recycle=settings.db_pool_recycle,
    pool_pre_ping=True,
)

SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped session.

    The session is rolled back and closed unconditionally on the way out, so a
    handler that raises can never leak a dirty connection back into the pool.
    Committing is the controller's job — it owns the transaction boundary.
    """
    session = SessionFactory()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def dispose_engine() -> None:
    """Close every pooled connection. Called on application shutdown."""
    await engine.dispose()


async def check_database(**_: Any) -> bool:
    """Lightweight readiness probe."""
    from sqlalchemy import text

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True
