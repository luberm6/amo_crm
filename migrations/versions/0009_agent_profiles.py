"""Add agent_profiles and link calls to agent profile

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-05
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("tone_rules", sa.Text(), nullable=True),
        sa.Column("business_rules", sa.Text(), nullable=True),
        sa.Column("sales_objectives", sa.Text(), nullable=True),
        sa.Column("greeting_text", sa.Text(), nullable=True),
        sa.Column("transfer_rules", sa.Text(), nullable=True),
        sa.Column("prohibited_promises", sa.Text(), nullable=True),
        sa.Column("voice_strategy", sa.String(length=32), nullable=False, server_default="tts_primary"),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
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
    op.create_index("ix_agent_profiles_name", "agent_profiles", ["name"])
    op.create_index("ix_agent_profiles_is_active", "agent_profiles", ["is_active"])
    op.create_index("ix_agent_profiles_created_at", "agent_profiles", ["created_at"])

    op.add_column(
        "calls",
        sa.Column("agent_profile_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_calls_agent_profile_id_agent_profiles",
        "calls",
        "agent_profiles",
        ["agent_profile_id"],
        ["id"],
    )
    op.create_index("ix_calls_agent_profile_id", "calls", ["agent_profile_id"])


def downgrade() -> None:
    op.drop_index("ix_calls_agent_profile_id", table_name="calls")
    op.drop_constraint("fk_calls_agent_profile_id_agent_profiles", "calls", type_="foreignkey")
    op.drop_column("calls", "agent_profile_id")

    op.drop_index("ix_agent_profiles_created_at", table_name="agent_profiles")
    op.drop_index("ix_agent_profiles_is_active", table_name="agent_profiles")
    op.drop_index("ix_agent_profiles_name", table_name="agent_profiles")
    op.drop_table("agent_profiles")
