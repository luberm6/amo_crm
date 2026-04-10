"""Add transfer hardening: failure_stage column on transfer_records

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-03

Changes:
  transfer_records — add failure_stage (machine-readable failure location)

New TransferStatus values (string enum — no migration needed):
  CALLER_DROPPED, BRIDGE_FAILED, TIMED_OUT

Rationale:
  failure_stage allows observability tooling to aggregate failures by phase:
    "no_managers", "dial", "dial_timeout", "bridge", "bridge_timeout",
    "caller_dropped"

  New terminal transfer statuses (CALLER_DROPPED, BRIDGE_FAILED, TIMED_OUT)
  are string values stored in the existing status column — no schema change needed
  since status is VARCHAR(30).
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transfer_records",
        sa.Column("failure_stage", sa.String(length=30), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transfer_records", "failure_stage")
