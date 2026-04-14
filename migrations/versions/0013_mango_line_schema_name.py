"""Add schema_name to telephony lines and normalize stored Mango numbers

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-14
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("telephony_lines", sa.Column("schema_name", sa.String(length=255), nullable=True))

    op.execute(
        """
        UPDATE telephony_lines
        SET schema_name = NULLIF(COALESCE(raw_payload->>'schema_name', raw_payload->>'schema', raw_payload->>'routing_schema_name'), '')
        WHERE provider = 'mango'
          AND schema_name IS NULL
        """
    )

    op.execute(
        """
        UPDATE telephony_lines
        SET phone_number = '+' || phone_number
        WHERE provider = 'mango'
          AND phone_number ~ '^7[0-9]{10}$'
          AND phone_number NOT LIKE '+%'
        """
    )

    op.execute(
        """
        UPDATE telephony_lines
        SET phone_number = '+7' || phone_number
        WHERE provider = 'mango'
          AND phone_number ~ '^[0-9]{10}$'
          AND phone_number NOT LIKE '+%'
        """
    )


def downgrade() -> None:
    op.drop_column("telephony_lines", "schema_name")
