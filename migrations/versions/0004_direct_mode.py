"""Add direct_session_id to calls; index mango_call_id

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-02

Changes:
  calls — add direct_session_id column (nullable str, indexed)
         — add index on mango_call_id (column exists since 0001, was unindexed)

Rationale:
  Direct mode хранит session_id в call.mango_call_id (поле уже существует).
  direct_session_id — явный alias для читаемости, хранит то же значение.
  INDEX на mango_call_id нужен для lookup сессии при stop/steer/status.
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Явный direct_session_id — alias для mango_call_id при Direct mode
    # Format: "{call_id}-direct"
    op.add_column(
        "calls",
        sa.Column("direct_session_id", sa.String(length=150), nullable=True),
    )
    op.create_index(
        "ix_calls_direct_session_id",
        "calls",
        ["direct_session_id"],
    )

    # mango_call_id уже есть в таблице с 0001, но индекса не было
    op.create_index(
        "ix_calls_mango_call_id",
        "calls",
        ["mango_call_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_calls_mango_call_id", table_name="calls")
    op.drop_index("ix_calls_direct_session_id", table_name="calls")
    op.drop_column("calls", "direct_session_id")
