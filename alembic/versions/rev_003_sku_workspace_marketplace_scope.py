"""scope workspace by marketplace

Revision ID: rev_003_marketplace_scope
Revises: rev_002_sku_workspace_versioning
Create Date: 2026-03-03 11:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "rev_003_marketplace_scope"
down_revision = "rev_002_sku_workspace_versioning"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    from sqlalchemy import inspect as sa_inspect
    cols = [c["name"] for c in sa_inspect(op.get_bind()).get_columns(table)]
    return column in cols


def _index_exists(name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :n"),
        {"n": name},
    )
    return result.scalar() is not None


def upgrade() -> None:
    if not _column_exists("sku_workspace", "marketplace_normalized"):
        op.add_column("sku_workspace", sa.Column("marketplace_normalized", sa.String(), nullable=True))
        op.execute(
            """
            UPDATE sku_workspace
            SET marketplace_normalized = LOWER(COALESCE(base_state->>'selected_marketplace', 'mercadolivre'))
            WHERE marketplace_normalized IS NULL OR marketplace_normalized = ''
            """
        )
        op.alter_column("sku_workspace", "marketplace_normalized", nullable=False)

    # Recreate index as non-unique (was unique in rev_002)
    if _index_exists("ix_sku_workspace_sku_normalized"):
        # Check if it's unique — drop and recreate as non-unique if needed
        conn = op.get_bind()
        is_unique = conn.execute(
            sa.text(
                "SELECT indisunique FROM pg_index JOIN pg_class ON pg_class.oid = pg_index.indexrelid "
                "WHERE pg_class.relname = 'ix_sku_workspace_sku_normalized'"
            )
        ).scalar()
        if is_unique:
            op.drop_index("ix_sku_workspace_sku_normalized", table_name="sku_workspace")
            op.create_index("ix_sku_workspace_sku_normalized", "sku_workspace", ["sku_normalized"], unique=False)
    else:
        op.create_index("ix_sku_workspace_sku_normalized", "sku_workspace", ["sku_normalized"], unique=False)

    if not _index_exists("ix_sku_workspace_marketplace_normalized"):
        op.create_index(
            "ix_sku_workspace_marketplace_normalized",
            "sku_workspace",
            ["marketplace_normalized"],
            unique=False,
        )
    if not _index_exists("ux_sku_workspace_sku_marketplace"):
        op.create_index(
            "ux_sku_workspace_sku_marketplace",
            "sku_workspace",
            ["sku_normalized", "marketplace_normalized"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index("ux_sku_workspace_sku_marketplace", table_name="sku_workspace")
    op.drop_index("ix_sku_workspace_marketplace_normalized", table_name="sku_workspace")
    op.drop_index("ix_sku_workspace_sku_normalized", table_name="sku_workspace")
    op.create_index("ix_sku_workspace_sku_normalized", "sku_workspace", ["sku_normalized"], unique=True)
    op.drop_column("sku_workspace", "marketplace_normalized")
