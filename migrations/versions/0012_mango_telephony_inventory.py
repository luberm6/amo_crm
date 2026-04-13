"""Add Mango telephony inventory and agent bindings

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-13
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telephony_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_resource_id", sa.String(length=128), nullable=False),
        sa.Column("phone_number", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("extension", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_inbound_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_outbound_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "provider",
            "provider_resource_id",
            name="uq_telephony_lines_provider_resource",
        ),
    )
    op.create_index("ix_telephony_lines_provider", "telephony_lines", ["provider"], unique=False)
    op.create_index("ix_telephony_lines_phone_number", "telephony_lines", ["phone_number"], unique=False)
    op.create_index("ix_telephony_lines_is_active", "telephony_lines", ["is_active"], unique=False)
    op.create_index("ix_telephony_lines_created_at", "telephony_lines", ["created_at"], unique=False)

    op.add_column(
        "agent_profiles",
        sa.Column(
            "voice_provider",
            sa.String(length=32),
            nullable=False,
            server_default="elevenlabs",
        ),
    )
    op.add_column("agent_profiles", sa.Column("telephony_provider", sa.String(length=32), nullable=True))
    op.add_column("agent_profiles", sa.Column("telephony_line_id", sa.Uuid(), nullable=True))
    op.add_column("agent_profiles", sa.Column("telephony_extension", sa.String(length=64), nullable=True))
    op.create_index("ix_agent_profiles_telephony_provider", "agent_profiles", ["telephony_provider"], unique=False)
    op.create_index("ix_agent_profiles_telephony_line_id", "agent_profiles", ["telephony_line_id"], unique=False)
    op.create_foreign_key(
        "fk_agent_profiles_telephony_line_id",
        "agent_profiles",
        "telephony_lines",
        ["telephony_line_id"],
        ["id"],
    )

    op.execute(
        """
        UPDATE agent_profiles
        SET voice_provider = CASE
            WHEN voice_strategy = 'gemini_primary' THEN 'gemini'
            ELSE 'elevenlabs'
        END
        """
    )
    op.alter_column("agent_profiles", "voice_provider", server_default=None)


def downgrade() -> None:
    op.drop_constraint("fk_agent_profiles_telephony_line_id", "agent_profiles", type_="foreignkey")
    op.drop_index("ix_agent_profiles_telephony_line_id", table_name="agent_profiles")
    op.drop_index("ix_agent_profiles_telephony_provider", table_name="agent_profiles")
    op.drop_column("agent_profiles", "telephony_extension")
    op.drop_column("agent_profiles", "telephony_line_id")
    op.drop_column("agent_profiles", "telephony_provider")
    op.drop_column("agent_profiles", "voice_provider")

    op.drop_index("ix_telephony_lines_created_at", table_name="telephony_lines")
    op.drop_index("ix_telephony_lines_is_active", table_name="telephony_lines")
    op.drop_index("ix_telephony_lines_phone_number", table_name="telephony_lines")
    op.drop_index("ix_telephony_lines_provider", table_name="telephony_lines")
    op.drop_table("telephony_lines")
