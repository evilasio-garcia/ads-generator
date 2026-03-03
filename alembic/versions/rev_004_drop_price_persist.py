"""drop persisted pricing from workspace snapshots

Revision ID: rev_004_drop_price_persist
Revises: rev_003_marketplace_scope
Create Date: 2026-03-03 12:30:00
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "rev_004_drop_price_persist"
down_revision = "rev_003_marketplace_scope"
branch_labels = None
depends_on = None


_EMPTY_PRICES = (
    '{"aggressive_min":{"versions":[],"current_index":-1},'
    '"promo_min":{"versions":[],"current_index":-1},'
    '"aggressive_max":{"versions":[],"current_index":-1},'
    '"promo_max":{"versions":[],"current_index":-1}}'
)


def upgrade() -> None:
    op.execute(
        """
        UPDATE sku_workspace
        SET versioned_state_current = COALESCE(versioned_state_current, '{}'::jsonb) - 'prices'
        """
    )
    op.execute(
        """
        UPDATE sku_workspace_history
        SET versioned_state_snapshot = COALESCE(versioned_state_snapshot, '{}'::jsonb) - 'prices'
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE sku_workspace
        SET versioned_state_current = jsonb_set(
            COALESCE(versioned_state_current, '{{}}'::jsonb),
            '{{prices}}',
            '{_EMPTY_PRICES}'::jsonb,
            true
        )
        WHERE NOT (COALESCE(versioned_state_current, '{{}}'::jsonb) ? 'prices')
        """
    )
    op.execute(
        f"""
        UPDATE sku_workspace_history
        SET versioned_state_snapshot = jsonb_set(
            COALESCE(versioned_state_snapshot, '{{}}'::jsonb),
            '{{prices}}',
            '{_EMPTY_PRICES}'::jsonb,
            true
        )
        WHERE NOT (COALESCE(versioned_state_snapshot, '{{}}'::jsonb) ? 'prices')
        """
    )
