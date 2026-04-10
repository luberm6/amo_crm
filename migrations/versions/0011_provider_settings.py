"""Add provider settings storage

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-05
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("secrets_encrypted", sa.Text(), nullable=False),
        sa.Column("validation_status", sa.String(length=32), nullable=False, server_default="not_tested"),
        sa.Column("last_validation_message", sa.Text(), nullable=True),
        sa.Column("last_validation_remote_checked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provider_settings_provider", "provider_settings", ["provider"], unique=True)
    op.create_index("ix_provider_settings_is_enabled", "provider_settings", ["is_enabled"])
    op.create_index("ix_provider_settings_created_at", "provider_settings", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_provider_settings_created_at", table_name="provider_settings")
    op.drop_index("ix_provider_settings_is_enabled", table_name="provider_settings")
    op.drop_index("ix_provider_settings_provider", table_name="provider_settings")
    op.drop_table("provider_settings")
