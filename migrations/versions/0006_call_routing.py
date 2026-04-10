"""Add call routing fields: route_used, telephony_leg_id

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-03

Changes:
  calls — add route_used (which engine handled the call: vapi/direct/stub)
        — add telephony_leg_id (provider SIP/Mango leg identifier)

Rationale:
  route_used is needed by RoutingCallEngine to resolve stable engine for
  stop/steer/get_status on existing calls. Without it, AUTO mode calls
  could be routed to the wrong engine after reconfiguration.

  telephony_leg_id stores the SIP Call-ID or Mango leg UID for
  cross-correlation between our DB, Vapi webhooks, and SIP logs.
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calls",
        sa.Column("route_used", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "calls",
        sa.Column("telephony_leg_id", sa.String(length=200), nullable=True),
    )
    op.create_index("ix_calls_route_used", "calls", ["route_used"])
    op.create_index("ix_calls_telephony_leg_id", "calls", ["telephony_leg_id"])


def downgrade() -> None:
    op.drop_index("ix_calls_telephony_leg_id", table_name="calls")
    op.drop_index("ix_calls_route_used", table_name="calls")
    op.drop_column("calls", "telephony_leg_id")
    op.drop_column("calls", "route_used")
