from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin, UUIDMixin


class ProviderSetting(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "provider_settings"

    provider: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    secrets_encrypted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_tested")
    last_validation_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_validation_remote_checked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<ProviderSetting id={self.id} provider={self.provider!r} "
            f"enabled={self.is_enabled} validation_status={self.validation_status!r}>"
        )
