"""baseline user_config

Revision ID: rev_001_baseline_user_config
Revises:
Create Date: 2026-03-03 00:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "rev_001_baseline_user_config"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_config",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_user_config_id", "user_config", ["id"], unique=False)
    op.create_index("ix_user_config_user_id", "user_config", ["user_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_user_config_user_id", table_name="user_config")
    op.drop_index("ix_user_config_id", table_name="user_config")
    op.drop_table("user_config")
