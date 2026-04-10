"""Add durable manager cooldown deadline (available_after)

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-04

Changes:
  managers — add available_after (timestamptz nullable) + index
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "managers",
        sa.Column("available_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_managers_available_after", "managers", ["available_after"])


def downgrade() -> None:
    op.drop_index("ix_managers_available_after", table_name="managers")
    op.drop_column("managers", "available_after")

