"""warm transfer — add manager availability/priority/department, create transfer_records

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-02

Changes:
  managers  — add is_available (bool), priority (int), department (str nullable)
  transfer_records — new table tracking warm transfer lifecycle
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── managers: add new columns ─────────────────────────────────────────────
    op.add_column(
        "managers",
        sa.Column(
            "is_available",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "managers",
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("5"),
        ),
    )
    op.add_column(
        "managers",
        sa.Column("department", sa.String(length=100), nullable=True),
    )
    op.create_index("ix_managers_department", "managers", ["department"])
    op.create_index("ix_managers_priority", "managers", ["priority"])

    # ── transfer_records: new table ───────────────────────────────────────────
    op.create_table(
        "transfer_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("manager_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="INITIATED",
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("whisper_text", sa.String(length=250), nullable=True),
        sa.Column("manager_call_id", sa.String(length=100), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("fallback_message", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"]),
        sa.ForeignKeyConstraint(["manager_id"], ["managers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_transfer_records_call_id", "transfer_records", ["call_id"])
    op.create_index("ix_transfer_records_status", "transfer_records", ["status"])


def downgrade() -> None:
    op.drop_index("ix_transfer_records_status", table_name="transfer_records")
    op.drop_index("ix_transfer_records_call_id", table_name="transfer_records")
    op.drop_table("transfer_records")

    op.drop_index("ix_managers_priority", table_name="managers")
    op.drop_index("ix_managers_department", table_name="managers")
    op.drop_column("managers", "department")
    op.drop_column("managers", "priority")
    op.drop_column("managers", "is_available")
