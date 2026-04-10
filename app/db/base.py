"""
SQLAlchemy declarative base and shared mixins.
All models import Base from here so Alembic can discover them via autogenerate.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
class Base(DeclarativeBase):
    """Project-wide declarative base."""
    pass
class UUIDMixin:
    """Adds a UUID primary key to any model. Uses SQLAlchemy Uuid type which
    maps to native UUID on Postgres and CHAR(32) on SQLite."""
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
class TimestampMixin:
    """Adds created_at / updated_at to any model. All times are UTC."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )