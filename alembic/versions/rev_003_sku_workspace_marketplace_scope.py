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


def upgrade() -> None:
    op.add_column("sku_workspace", sa.Column("marketplace_normalized", sa.String(), nullable=True))
    op.execute(
        """
        UPDATE sku_workspace
        SET marketplace_normalized = LOWER(COALESCE(base_state->>'selected_marketplace', 'mercadolivre'))
        WHERE marketplace_normalized IS NULL OR marketplace_normalized = ''
        """
    )
    op.alter_column("sku_workspace", "marketplace_normalized", nullable=False)

    op.drop_index("ix_sku_workspace_sku_normalized", table_name="sku_workspace")
    op.create_index("ix_sku_workspace_sku_normalized", "sku_workspace", ["sku_normalized"], unique=False)
    op.create_index(
        "ix_sku_workspace_marketplace_normalized",
        "sku_workspace",
        ["marketplace_normalized"],
        unique=False,
    )
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
