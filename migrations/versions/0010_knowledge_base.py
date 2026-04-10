"""Add company profiles, knowledge documents, and agent knowledge bindings

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-05
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("value_proposition", sa.Text(), nullable=True),
        sa.Column("target_audience", sa.Text(), nullable=True),
        sa.Column("contact_info", sa.Text(), nullable=True),
        sa.Column("website_url", sa.String(length=255), nullable=True),
        sa.Column("working_hours", sa.Text(), nullable=True),
        sa.Column("compliance_notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("config", sa.JSON(), nullable=True),
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
    op.create_index("ix_company_profiles_created_at", "company_profiles", ["created_at"])

    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
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
    op.create_index("ix_knowledge_documents_title", "knowledge_documents", ["title"])
    op.create_index("ix_knowledge_documents_category", "knowledge_documents", ["category"])
    op.create_index("ix_knowledge_documents_is_active", "knowledge_documents", ["is_active"])
    op.create_index("ix_knowledge_documents_created_at", "knowledge_documents", ["created_at"])

    op.create_table(
        "agent_knowledge_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_profile_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_document_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=True),
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
        sa.ForeignKeyConstraint(["agent_profile_id"], ["agent_profiles.id"]),
        sa.ForeignKeyConstraint(["knowledge_document_id"], ["knowledge_documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_knowledge_bindings_agent_profile_id",
        "agent_knowledge_bindings",
        ["agent_profile_id"],
    )
    op.create_index(
        "ix_agent_knowledge_bindings_knowledge_document_id",
        "agent_knowledge_bindings",
        ["knowledge_document_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_knowledge_bindings_knowledge_document_id", table_name="agent_knowledge_bindings")
    op.drop_index("ix_agent_knowledge_bindings_agent_profile_id", table_name="agent_knowledge_bindings")
    op.drop_table("agent_knowledge_bindings")

    op.drop_index("ix_knowledge_documents_created_at", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_is_active", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_category", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_title", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")

    op.drop_index("ix_company_profiles_created_at", table_name="company_profiles")
    op.drop_table("company_profiles")
