"""add mercadolivre_category_tree_cache table

Revision ID: rev_008_mercadolivre_category_tree_cache
Revises: rev_007_ml_category_baseline
Create Date: 2026-03-12 10:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "rev_008_mercadolivre_category_tree_cache"
down_revision = "rev_007_ml_category_baseline"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    from sqlalchemy import inspect as sa_inspect
    return name in sa_inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _table_exists("mercadolivre_category_tree_cache"):
        op.create_table(
            "mercadolivre_category_tree_cache",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("site_id", sa.String(), nullable=False),
            sa.Column(
                "tree_data",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column("node_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("loaded_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("site_id", name="ux_mercadolivre_category_tree_cache_site"),
        )


def downgrade() -> None:
    op.drop_table("mercadolivre_category_tree_cache")
