#!/usr/bin/env python
"""
Extract Tiny product payloads for 2+ SKUs and save a local fixture JSON.

Usage examples:
  python scripts/extract_tiny_sku_fixture.py --sku NEWGD60C7 --sku PTBOCSALMCATCX10
  python scripts/extract_tiny_sku_fixture.py --sku A --sku B --user-id <uuid> --instance-index 0
  python scripts/extract_tiny_sku_fixture.py --sku A --sku B --token <tiny_token> --include-raw
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tiny_service


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract Tiny SKU data into fixture JSON.")
    parser.add_argument("--sku", action="append", required=True, help="SKU to fetch (repeat the flag).")
    parser.add_argument("--token", default="", help="Tiny token. If omitted, script reads token from user_config in DB.")
    parser.add_argument("--user-id", default="", help="User ID in user_config. If omitted, uses latest config row.")
    parser.add_argument("--instance-index", type=int, default=0, help="Index inside data.tiny_tokens (default: 0).")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", ""),
        help="Database URL used when token is omitted. Defaults to env DATABASE_URL.",
    )
    parser.add_argument(
        "--output",
        default="tests/fixtures/tiny/tiny_sku_fixture.json",
        help="Output fixture path.",
    )
    parser.add_argument(
        "--include-raw",
        action="store_true",
        help="Include raw_data from Tiny in the fixture.",
    )
    return parser.parse_args()


def _ensure_skus(raw_skus: List[str]) -> List[str]:
    normalized = []
    seen = set()
    for raw in raw_skus:
        sku = str(raw or "").strip().upper()
        if not sku or sku in seen:
            continue
        normalized.append(sku)
        seen.add(sku)
    if len(normalized) < 2:
        raise ValueError("Provide at least 2 SKUs via --sku.")
    return normalized


def _extract_token_from_user_config(
    database_url: str,
    user_id: str,
    instance_index: int,
) -> str:
    if not database_url:
        raise RuntimeError("DATABASE_URL not provided. Use --database-url or --token.")

    engine = create_engine(database_url, future=True)
    query_by_user = text("SELECT data FROM user_config WHERE user_id = :user_id LIMIT 1")
    query_latest = text(
        "SELECT data FROM user_config "
        "ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST "
        "LIMIT 1"
    )

    with engine.connect() as conn:
        row = conn.execute(query_by_user, {"user_id": user_id}).first() if user_id else conn.execute(query_latest).first()

    if not row:
        raise RuntimeError("No user_config row found in database.")

    cfg_data = row[0]
    if isinstance(cfg_data, str):
        cfg_data = json.loads(cfg_data)
    if not isinstance(cfg_data, dict):
        raise RuntimeError("Invalid user_config.data format.")

    tiny_tokens = cfg_data.get("tiny_tokens") or []
    if not isinstance(tiny_tokens, list) or not tiny_tokens:
        raise RuntimeError("No tiny_tokens configured in user_config.")
    if instance_index < 0 or instance_index >= len(tiny_tokens):
        raise RuntimeError(
            f"instance_index={instance_index} out of range for tiny_tokens ({len(tiny_tokens)} entries)."
        )

    token = str((tiny_tokens[instance_index] or {}).get("token") or "").strip()
    if not token:
        raise RuntimeError(f"Token missing at tiny_tokens[{instance_index}].")
    return token


def _compact_product_payload(payload: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    keep_keys = [
        "title",
        "sku",
        "gtin",
        "height_cm",
        "width_cm",
        "length_cm",
        "weight_kg",
        "cost_price",
        "list_price",
        "promo_price",
    ]
    out = {k: payload.get(k) for k in keep_keys}
    if include_raw:
        out["raw_data"] = payload.get("raw_data")
    return out


async def _collect_products(token: str, skus: List[str], include_raw: bool) -> Dict[str, Any]:
    products: Dict[str, Any] = {}
    errors: Dict[str, Any] = {}
    for sku in skus:
        try:
            payload = await tiny_service.get_product_by_sku(token=token, sku=sku)
            products[sku] = _compact_product_payload(payload, include_raw=include_raw)
        except Exception as exc:  # noqa: BLE001
            errors[sku] = {"error": str(exc), "type": exc.__class__.__name__}
    return {"products": products, "errors": errors}


def _write_fixture(path: Path, fixture: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")


async def _main() -> None:
    args = _parse_args()
    skus = _ensure_skus(args.sku)

    token = str(args.token or "").strip()
    token_source = "cli"
    if not token:
        token = _extract_token_from_user_config(
            database_url=args.database_url,
            user_id=args.user_id,
            instance_index=args.instance_index,
        )
        token_source = "db_user_config"

    result = await _collect_products(token=token, skus=skus, include_raw=args.include_raw)
    fixture = {
        "schema": "tiny_product_fixture_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "token_source": token_source,
        "user_id": args.user_id or None,
        "instance_index": args.instance_index,
        "skus_requested": skus,
        "products": result["products"],
        "errors": result["errors"],
    }

    out_path = Path(args.output)
    _write_fixture(out_path, fixture)
    print(f"Fixture written to: {out_path}")
    print(f"Products captured: {len(result['products'])} / {len(skus)}")
    if result["errors"]:
        print("Errors:")
        for sku, err in result["errors"].items():
            print(f"- {sku}: {err['type']} - {err['error']}")


if __name__ == "__main__":
    asyncio.run(_main())
