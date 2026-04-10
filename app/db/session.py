"""
Async SQLAlchemy engine and session factory.
Uses asyncpg driver for high-throughput async I/O.
"""
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.core.config import settings
# Engine is created once and reused for the lifetime of the process.
# pool_pre_ping ensures stale connections are recycled automatically.
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False,  # Set True locally for SQL debugging
)
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Avoids lazy-load errors after commit
    autocommit=False,
    autoflush=False,
)
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields an async session and commits on success."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise