"""Add widget_configs and widget_leads tables

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-19
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "widget_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("widget_token", sa.String(length=64), nullable=False),
        sa.Column("agent_profile_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("allowed_domains", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("rate_limit_per_hour", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("rate_limit_per_ip_per_hour", sa.Integer(), nullable=False, server_default=sa.text("10")),
        sa.Column("lead_capture_fields", sa.JSON(), nullable=True),
        sa.Column("webhook_url", sa.Text(), nullable=True),
        sa.Column("telegram_chat_id", sa.String(length=64), nullable=True),
        sa.Column("custom_greeting", sa.Text(), nullable=True),
        sa.Column("custom_styles", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_profile_id"], ["agent_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_widget_configs_widget_token", "widget_configs", ["widget_token"], unique=True)
    op.create_index("ix_widget_configs_agent_profile_id", "widget_configs", ["agent_profile_id"])
    op.create_index("ix_widget_configs_is_active", "widget_configs", ["is_active"])
    op.create_index("ix_widget_configs_created_at", "widget_configs", ["created_at"])

    op.create_table(
        "widget_leads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("widget_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("email", sa.String(length=200), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("extra_fields", sa.JSON(), nullable=True),
        sa.Column("webhook_delivered", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("telegram_delivered", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["widget_id"], ["widget_configs.id"]),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_widget_leads_widget_id", "widget_leads", ["widget_id"])
    op.create_index("ix_widget_leads_call_id", "widget_leads", ["call_id"])
    op.create_index("ix_widget_leads_created_at", "widget_leads", ["created_at"])


def downgrade() -> None:
    op.drop_table("widget_leads")
    op.drop_table("widget_configs")
