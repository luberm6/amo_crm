"""add transcript_entries and vapi_event_logs tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── transcript_entries ────────────────────────────────────────────────────
    op.create_table(
        "transcript_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("sequence_num", sa.Integer(), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_transcript_entries_call_id",
        "transcript_entries",
        ["call_id"],
    )
    op.create_index(
        "ix_transcript_entries_call_id_seq",
        "transcript_entries",
        ["call_id", "sequence_num"],
    )

    # ── vapi_event_logs ───────────────────────────────────────────────────────
    op.create_table(
        "vapi_event_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("vapi_event_id", sa.String(length=200), nullable=True),
        sa.Column("call_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column(
            "processing_status", sa.String(length=20), nullable=False
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vapi_event_id"),
    )
    op.create_index(
        "ix_vapi_event_logs_call_id",
        "vapi_event_logs",
        ["call_id"],
    )
    op.create_index(
        "ix_vapi_event_logs_event_type",
        "vapi_event_logs",
        ["event_type"],
    )
    op.create_index(
        "ix_vapi_event_logs_received_at",
        "vapi_event_logs",
        ["received_at"],
    )


def downgrade() -> None:
    op.drop_table("vapi_event_logs")
    op.drop_table("transcript_entries")
