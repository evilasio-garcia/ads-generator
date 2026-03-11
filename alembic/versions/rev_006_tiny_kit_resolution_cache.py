"""add tiny kit resolution global cache

Revision ID: rev_006_tiny_kit_resolution_cache
Revises: rev_005_workspace_variants_schema_v2
Create Date: 2026-03-04 11:30:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "rev_006_tiny_kit_resolution_cache"
down_revision = "rev_005_workspace_variants_schema_v2"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    from sqlalchemy import inspect as sa_inspect
    return name in sa_inspect(op.get_bind()).get_table_names()


def _index_exists(name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :n"),
        {"n": name},
    )
    return result.scalar() is not None


def upgrade() -> None:
    if not _table_exists("tiny_kit_resolution"):
        op.create_table(
            "tiny_kit_resolution",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("sku_root_normalized", sa.String(), nullable=False),
            sa.Column("kit_quantity", sa.Integer(), nullable=False),
            sa.Column("resolved_sku", sa.String(), nullable=False),
            sa.Column("validation_source", sa.String(), nullable=False, server_default="pattern_skucb"),
            sa.Column("unit_plural_override", sa.String(), nullable=True),
            sa.Column("tiny_product_id", sa.String(), nullable=True),
            sa.Column("validation_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("validated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.Column("last_checked_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("sku_root_normalized", "kit_quantity", name="ux_tiny_kit_resolution_sku_qty"),
        )
    if not _index_exists("ix_tiny_kit_resolution_sku_root_normalized"):
        op.create_index(op.f("ix_tiny_kit_resolution_sku_root_normalized"), "tiny_kit_resolution", ["sku_root_normalized"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_tiny_kit_resolution_sku_root_normalized"), table_name="tiny_kit_resolution")
    op.drop_table("tiny_kit_resolution")
