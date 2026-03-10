"""add ml_category_baseline table

Revision ID: rev_007_ml_category_baseline
Revises: rev_006_tiny_kit_resolution_cache
Create Date: 2026-03-10 10:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "rev_007_ml_category_baseline"
down_revision = "rev_006_tiny_kit_resolution_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ml_category_baseline",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("category_id", sa.String(), nullable=False),
        sa.Column("required_attr_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("conditional_attr_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("hidden_writable_attr_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("full_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "category_id", name="ux_ml_category_baseline_user_cat"),
    )
    op.create_index(op.f("ix_ml_category_baseline_user_id"), "ml_category_baseline", ["user_id"], unique=False)
    op.create_index(op.f("ix_ml_category_baseline_category_id"), "ml_category_baseline", ["category_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ml_category_baseline_category_id"), table_name="ml_category_baseline")
    op.drop_index(op.f("ix_ml_category_baseline_user_id"), table_name="ml_category_baseline")
    op.drop_table("ml_category_baseline")
