"""migrate workspace snapshots to variants schema v2

Revision ID: rev_005_workspace_variants_schema_v2
Revises: rev_004_drop_price_persist
Create Date: 2026-03-03 18:30:00
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = "rev_005_workspace_variants_schema_v2"
down_revision = "rev_004_drop_price_persist"
branch_labels = None
depends_on = None


VARIANT_KEYS = ("simple", "kit2", "kit3", "kit4", "kit5")


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _coerce_index(value: Any, size: int, fallback_last: bool = False) -> int:
    try:
        idx = int(value)
    except (TypeError, ValueError):
        idx = -1
    if size <= 0:
        return -1
    if idx < 0:
        return size - 1 if fallback_last else -1
    if idx >= size:
        return size - 1
    return idx


def _normalize_text_block(raw: Any) -> Dict[str, Any]:
    raw_d = _safe_dict(raw)
    versions = [str(v) if v is not None else "" for v in _safe_list(raw_d.get("versions"))]
    idx = _coerce_index(raw_d.get("current_index"), len(versions), fallback_last=True)
    return {"versions": versions, "current_index": idx}


def _normalize_faq_line(raw: Any) -> Dict[str, Any]:
    raw_d = _safe_dict(raw)
    versions: List[Dict[str, str]] = []
    for item in _safe_list(raw_d.get("versions")):
        item_d = _safe_dict(item)
        versions.append({"q": str(item_d.get("q") or ""), "a": str(item_d.get("a") or "")})
    if not versions:
        versions = [{"q": "", "a": ""}]
    idx = _coerce_index(raw_d.get("current_index"), len(versions), fallback_last=True)
    return {"approved": bool(raw_d.get("approved", True)), "versions": versions, "current_index": idx}


def _normalize_card_line(raw: Any) -> Dict[str, Any]:
    raw_d = _safe_dict(raw)
    versions: List[Dict[str, str]] = []
    for item in _safe_list(raw_d.get("versions")):
        item_d = _safe_dict(item)
        versions.append({"title": str(item_d.get("title") or ""), "text": str(item_d.get("text") or "")})
    if not versions:
        versions = [{"title": "", "text": ""}]
    idx = _coerce_index(raw_d.get("current_index"), len(versions), fallback_last=True)
    return {"versions": versions, "current_index": idx}


def _empty_variant_state() -> Dict[str, Any]:
    return {
        "title": {"versions": [], "current_index": -1},
        "description": {"versions": [], "current_index": -1},
        "faq_lines": [],
        "card_lines": [],
    }


def _normalize_variant_state(raw: Any) -> Dict[str, Any]:
    raw_d = _safe_dict(raw)
    return {
        "title": _normalize_text_block(raw_d.get("title")),
        "description": _normalize_text_block(raw_d.get("description")),
        "faq_lines": [_normalize_faq_line(x) for x in _safe_list(raw_d.get("faq_lines"))],
        "card_lines": [_normalize_card_line(x) for x in _safe_list(raw_d.get("card_lines"))],
    }


def _convert_to_v2(raw_state: Any) -> Dict[str, Any]:
    raw_d = _safe_dict(raw_state)
    variants_raw = _safe_dict(raw_d.get("variants"))
    out_variants = {key: _empty_variant_state() for key in VARIANT_KEYS}

    if variants_raw:
        for key in VARIANT_KEYS:
            out_variants[key] = _normalize_variant_state(variants_raw.get(key))
    else:
        out_variants["simple"] = _normalize_variant_state(raw_d)

    return {"schema_version": 2, "variants": out_variants, "prices": {}}


def _convert_to_v1(raw_state: Any) -> Dict[str, Any]:
    raw_d = _safe_dict(raw_state)
    variants_raw = _safe_dict(raw_d.get("variants"))
    if variants_raw:
        simple = _normalize_variant_state(variants_raw.get("simple"))
    else:
        simple = _normalize_variant_state(raw_d)
    simple["prices"] = {}
    return simple


def _migrate_column_to_v2(table_name: str, column_name: str) -> None:
    conn = op.get_bind()
    rows = conn.execute(text(f"SELECT id, {column_name} FROM {table_name}")).mappings().all()
    for row in rows:
        payload = _convert_to_v2(row.get(column_name))
        conn.execute(
            text(f"UPDATE {table_name} SET {column_name} = CAST(:payload AS jsonb) WHERE id = :row_id"),
            {"payload": json.dumps(payload, ensure_ascii=False), "row_id": row["id"]},
        )


def _migrate_column_to_v1(table_name: str, column_name: str) -> None:
    conn = op.get_bind()
    rows = conn.execute(text(f"SELECT id, {column_name} FROM {table_name}")).mappings().all()
    for row in rows:
        payload = _convert_to_v1(row.get(column_name))
        conn.execute(
            text(f"UPDATE {table_name} SET {column_name} = CAST(:payload AS jsonb) WHERE id = :row_id"),
            {"payload": json.dumps(payload, ensure_ascii=False), "row_id": row["id"]},
        )


def upgrade() -> None:
    # Alguns ambientes legados possuem alembic_version.version_num como VARCHAR(32),
    # mas os revision IDs atuais excedem esse limite.
    op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)")
    _migrate_column_to_v2("sku_workspace", "versioned_state_current")
    _migrate_column_to_v2("sku_workspace_history", "versioned_state_snapshot")


def downgrade() -> None:
    _migrate_column_to_v1("sku_workspace", "versioned_state_current")
    _migrate_column_to_v1("sku_workspace_history", "versioned_state_snapshot")
