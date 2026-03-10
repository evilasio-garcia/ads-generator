"""
Fetch published ML items, their fees, and shipping costs via API.
Usage: python scripts/fetch_ml_item.py [MLB_ID1 MLB_ID2 ...]
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from mercadolivre_service import get_valid_access_token
from config import settings

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://app_ads_generator_usr:app_ads_generator_psw@localhost:5432/app_ads_generator_db",
)
engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

from app import UserConfig

ML_API_BASE = "https://api.mercadolibre.com"
USER_ID = "182bd036-b52b-40ce-b027-b34e0451f64c"
ML_USER_ID = "1452969010"

DEFAULT_ITEMS = ["MLB6428824842", "MLB4613470360"]


def has_mandatory_free_shipping(shipping: dict) -> bool:
    """Detect if listing has mandatory free shipping based on shipping tags."""
    tags = shipping.get("tags") or []
    return "mandatory_free_shipping" in tags


async def main():
    item_ids = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_ITEMS

    # Get access token from DB
    db = SessionLocal()
    try:
        cfg_row = db.query(UserConfig).filter(UserConfig.user_id == USER_ID).first()
        if not cfg_row:
            print("UserConfig not found")
            return
        ml_accounts = (cfg_row.data or {}).get("ml_accounts") or []
        if not ml_accounts:
            print("No ML accounts configured")
            return
        account = ml_accounts[0]
    finally:
        db.close()

    access_token, updated = await get_valid_access_token(
        account, settings.ml_client_id, settings.ml_client_secret
    )

    if updated:
        db = SessionLocal()
        try:
            cfg_row = db.query(UserConfig).filter(UserConfig.user_id == USER_ID).first()
            ml_accounts = list((cfg_row.data or {}).get("ml_accounts") or [])
            ml_accounts[0] = updated
            data = dict(cfg_row.data or {})
            data["ml_accounts"] = ml_accounts
            cfg_row.data = data
            db.commit()
            print("[Token refreshed and saved]")
        finally:
            db.close()

    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        for item_id in item_ids:
            # 1. Item details
            print(f"\n{'#'*60}")
            print(f"# ITEM: {item_id}")
            print(f"{'#'*60}")
            resp = await client.get(f"{ML_API_BASE}/items/{item_id}", headers=headers)
            item_data = resp.json()
            print(json.dumps(item_data, indent=2, ensure_ascii=False))

            # 2. Listing fees
            listing_type = item_data.get("listing_type_id", "gold_special")
            category_id = item_data.get("category_id", "")
            price = item_data.get("price", 0)
            print(f"\n{'='*60}")
            print(f"LISTING FEES (category={category_id}, type={listing_type})")
            print(f"{'='*60}")
            resp2 = await client.get(
                f"{ML_API_BASE}/sites/MLB/listing_prices",
                params={"price": price, "listing_type_id": listing_type, "category_id": category_id},
                headers=headers,
            )
            print(f"Status: {resp2.status_code}")
            print(json.dumps(resp2.json(), indent=2, ensure_ascii=False))

            # 3. Free shipping cost for seller
            shipping = item_data.get("shipping") or {}
            free_shipping = has_mandatory_free_shipping(shipping)
            print(f"\n{'='*60}")
            print(f"FREE SHIPPING OPTIONS (free_shipping={free_shipping})")
            print(f"{'='*60}")
            resp3 = await client.get(
                f"{ML_API_BASE}/users/{ML_USER_ID}/shipping_options/free",
                params={"item_id": item_id, "free_shipping": str(free_shipping).lower()},
                headers=headers,
            )
            print(f"Status: {resp3.status_code}")
            print(json.dumps(resp3.json(), indent=2, ensure_ascii=False))

            print(f"\n{'~'*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
