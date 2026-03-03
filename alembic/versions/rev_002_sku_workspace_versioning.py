"""sku workspace versioning

Revision ID: rev_002_sku_workspace_versioning
Revises: rev_001_baseline_user_config
Create Date: 2026-03-03 00:10:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "rev_002_sku_workspace_versioning"
down_revision = "rev_001_baseline_user_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sku_workspace",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("sku_normalized", sa.String(), nullable=False),
        sa.Column("sku_display", sa.String(), nullable=False),
        sa.Column(
            "base_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "versioned_state_current",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("state_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by_user_id", sa.String(), nullable=False),
        sa.Column("updated_by_user_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_sku_workspace_id", "sku_workspace", ["id"], unique=False)
    op.create_index("ix_sku_workspace_sku_normalized", "sku_workspace", ["sku_normalized"], unique=True)

    op.create_table(
        "sku_workspace_history",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("created_by_user_id", sa.String(), nullable=False),
        sa.Column(
            "versioned_state_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("snapshot_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["sku_workspace.id"]),
    )
    op.create_index("ix_sku_workspace_history_id", "sku_workspace_history", ["id"], unique=False)
    op.create_index("ix_sku_workspace_history_workspace_id", "sku_workspace_history", ["workspace_id"], unique=False)
    op.create_index("ix_sku_workspace_history_snapshot_hash", "sku_workspace_history", ["snapshot_hash"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sku_workspace_history_snapshot_hash", table_name="sku_workspace_history")
    op.drop_index("ix_sku_workspace_history_workspace_id", table_name="sku_workspace_history")
    op.drop_index("ix_sku_workspace_history_id", table_name="sku_workspace_history")
    op.drop_table("sku_workspace_history")

    op.drop_index("ix_sku_workspace_sku_normalized", table_name="sku_workspace")
    op.drop_index("ix_sku_workspace_id", table_name="sku_workspace")
    op.drop_table("sku_workspace")
