"""Add blocked_phones deny list table

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-02

Changes:
  blocked_phones — new table for outbound call deny list
    - id (UUID PK)
    - phone (E.164, unique, indexed)
    - reason (text, nullable)
    - added_by (str)
    - created_at (timestamptz)
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "blocked_phones",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("added_by", sa.String(length=100), nullable=False, server_default="system"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("phone"),
    )
    op.create_index("ix_blocked_phones_phone", "blocked_phones", ["phone"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_blocked_phones_phone", table_name="blocked_phones")
    op.drop_table("blocked_phones")
