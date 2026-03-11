"""
Debug ML seller promotions API.
Usage:
  python scripts/debug_ml_promotions.py                        # list promotions + candidates
  python scripts/debug_ml_promotions.py MLB6434620432 120.00   # add item to first SELLER_CAMPAIGN
  python scripts/debug_ml_promotions.py --check MLB6434620432  # check item's current promotions
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import settings
from mercadolivre_service import (
    add_item_to_promotion,
    get_seller_own_promotions,
    get_valid_access_token,
)

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


async def get_token():
    db = SessionLocal()
    try:
        cfg_row = db.query(UserConfig).filter(UserConfig.user_id == USER_ID).first()
        if not cfg_row:
            print("UserConfig not found")
            sys.exit(1)
        ml_accounts = (cfg_row.data or {}).get("ml_accounts") or []
        if not ml_accounts:
            print("No ML accounts configured")
            sys.exit(1)
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

    return access_token


async def list_promotions_and_candidates(access_token):
    headers = {"Authorization": f"Bearer {access_token}"}

    # 1. List seller promotions
    print(f"\n{'='*60}")
    print("SELLER PROMOTIONS (active + pending)")
    print(f"{'='*60}")
    promos = await get_seller_own_promotions(access_token, ML_USER_ID)
    if not promos:
        print("  No promotions found.")
        return promos

    for p in promos:
        print(f"\n  ID: {p.get('id')}")
        print(f"  Type: {p.get('type')}")
        print(f"  Name: {p.get('name', 'N/A')}")
        print(f"  Status: {p.get('status')}")
        print(f"  Start: {p.get('start_date', 'N/A')}")
        print(f"  End: {p.get('finish_date', 'N/A')}")

    # 2. For each SELLER_CAMPAIGN, list candidates
    seller_campaigns = [p for p in promos if p.get("type") == "SELLER_CAMPAIGN"]
    async with httpx.AsyncClient(timeout=30.0) as client:
        for camp in seller_campaigns:
            promo_id = camp["id"]
            for status in ("candidate", "started", "pending"):
                print(f"\n{'-'*60}")
                print(f"ITEMS in {promo_id} ({camp.get('name', 'N/A')}) status={status}")
                print(f"{'-'*60}")
                resp = await client.get(
                    f"{ML_API_BASE}/seller-promotions/promotions/{promo_id}/items",
                    params={
                        "promotion_type": "SELLER_CAMPAIGN",
                        "app_version": "v2",
                        "status": status,
                    },
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("results") or []
                    print(f"  Count: {len(results)}")
                    for item in results[:10]:  # Show first 10
                        print(f"  - {item.get('id', 'N/A')}: price={item.get('price')}, "
                              f"original={item.get('original_price')}, status={item.get('status')}")
                    if len(results) > 10:
                        print(f"  ... and {len(results) - 10} more")
                    paging = data.get("paging") or {}
                    print(f"  Total: {paging.get('total', len(results))}")
                else:
                    print(f"  Status: {resp.status_code}")
                    print(f"  Response: {json.dumps(resp.json(), indent=2, ensure_ascii=False)}")

    return promos


async def check_item_promotions(access_token, item_id):
    """Check what promotions an item is currently in or candidate for."""
    headers = {"Authorization": f"Bearer {access_token}"}
    print(f"\n{'='*60}")
    print(f"PROMOTIONS FOR ITEM {item_id}")
    print(f"{'='*60}")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{ML_API_BASE}/seller-promotions/items/{item_id}",
            params={"app_version": "v2"},
            headers=headers,
        )
        print(f"  Status: {resp.status_code}")
        print(f"  Response: {json.dumps(resp.json(), indent=2, ensure_ascii=False)}")


async def add_to_promotion(access_token, item_id, deal_price, promos):
    seller_campaigns = [p for p in promos if p.get("type") == "SELLER_CAMPAIGN"]
    if not seller_campaigns:
        print("\n  No SELLER_CAMPAIGN found.")
        return

    target = seller_campaigns[0]
    print(f"\n{'='*60}")
    print(f"ADDING {item_id} to {target['id']} ({target.get('name', 'N/A')})")
    print(f"  deal_price: {deal_price}")
    print(f"{'='*60}")

    # First check if item is a candidate
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        check_resp = await client.get(
            f"{ML_API_BASE}/seller-promotions/promotions/{target['id']}/items",
            params={
                "promotion_type": "SELLER_CAMPAIGN",
                "app_version": "v2",
                "status": "candidate",
                "item_id": item_id,
            },
            headers=headers,
        )
        print(f"\n  Candidate check: {check_resp.status_code}")
        check_data = check_resp.json()
        candidates = check_data.get("results") or []
        if candidates:
            print(f"  Item IS a candidate: {json.dumps(candidates[0], indent=2, ensure_ascii=False)}")
        else:
            print(f"  Item is NOT a candidate for this campaign")
            print(f"  Response: {json.dumps(check_data, indent=2, ensure_ascii=False)}")

        # Try adding anyway to see the actual error
        print(f"\n  Attempting POST...")
        resp = await client.post(
            f"{ML_API_BASE}/seller-promotions/items/{item_id}",
            params={"app_version": "v2"},
            headers={**headers, "Content-Type": "application/json"},
            json={
                "promotion_id": target["id"],
                "promotion_type": target["type"],
                "deal_price": deal_price,
            },
        )
        print(f"  Status: {resp.status_code}")
        print(f"  Response: {json.dumps(resp.json(), indent=2, ensure_ascii=False)}")


async def candidacy_timing_test(access_token):
    """Create a listing for kit 2 of NEWGD60C7 and poll for candidacy every second."""
    import time
    from mercadolivre_service import create_listing, activate_listing

    headers = {"Authorization": f"Bearer {access_token}"}

    # Get the first SELLER_CAMPAIGN
    promos = await get_seller_own_promotions(access_token, ML_USER_ID)
    seller_campaigns = [p for p in promos if p.get("type") == "SELLER_CAMPAIGN"]
    if not seller_campaigns:
        print("No SELLER_CAMPAIGN found.")
        return
    campaign = seller_campaigns[0]
    promo_id = campaign["id"]
    print(f"Target campaign: {promo_id} ({campaign.get('name', 'N/A')})")

    # Get pictures from an existing listing to reuse
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{ML_API_BASE}/items/MLB6435112658",
            params={"attributes": "pictures"},
            headers=headers,
        )
        pics = resp.json().get("pictures", [])
        picture_ids = [{"id": p["id"]} for p in pics[:2]]
    print(f"Reusing {len(picture_ids)} pictures from MLB6435112658")

    # Build minimal payload for kit 2 of NEWGD60C7
    # Kit 2: weight*2=0.4kg, width*2=60cm, cost*2=49.80
    # MLB178930 is catalog_required: use family_name + attributes
    listing_payload = {
        "family_name": "COMBO 2 PACOTES NEW GOOD 60X60 7UN TESTE",
        "category_id": "MLB178930",
        "price": 216.99,
        "currency_id": "BRL",
        "available_quantity": 1,
        "condition": "new",
        "listing_type_id": "gold_special",
        "status": "paused",
        "pictures": picture_ids,
        "attributes": [
            {"id": "SELLER_SKU", "value_name": "NEWGD60C7CB2-TEST"},
            {"id": "BRAND", "value_id": "22797700", "value_name": "New Good"},
            {"id": "MODEL", "value_id": "23750720", "value_name": "Good Pad"},
            {"id": "COLOR", "value_id": "11282031", "value_name": "Branco"},
            {"id": "PATTERN_NAME", "value_id": "24721100", "value_name": "Liso"},
            {"id": "SALE_FORMAT", "value_name": "Kit"},
            {"id": "UNITS_PER_PACK", "value_name": "2"},
            {"id": "UNITS_PER_PACKAGE", "value_id": "235716", "value_name": "7"},
            {"id": "LENGTH", "value_id": "908317", "value_name": "60 cm"},
            {"id": "WIDTH", "value_id": "908317", "value_name": "60 cm"},
            {"id": "PACKS_NUMBER", "value_name": "1"},
            {"id": "MATERIAL", "value_id": "52236114", "value_name": "Papel tissue"},
            {"id": "LAYERS_NUMBER", "value_id": "19097507", "value_name": "6"},
            {"id": "IS_DISPOSABLE", "value_id": "242085", "value_name": "Sim"},
            {"id": "IS_WASHABLE", "value_id": "242084"},
            {"id": "WITH_ODOR_ELIMINATION", "value_id": "242085", "value_name": "Sim"},
            {"id": "SELLER_PACKAGE_HEIGHT", "value_name": "52 cm"},
            {"id": "SELLER_PACKAGE_WIDTH", "value_name": "60 cm"},
            {"id": "SELLER_PACKAGE_LENGTH", "value_name": "18 cm"},
            {"id": "SELLER_PACKAGE_WEIGHT", "value_name": "400 g"},
        ],
        "sale_terms": [
            {"id": "WARRANTY_TYPE", "value_id": "2230279"},
            {"id": "WARRANTY_TIME", "value_name": "30 dias"},
        ],
    }

    # 1. Create listing (paused)
    print(f"\n{'='*60}")
    print("STEP 1: Creating listing (paused)...")
    print(f"{'='*60}")
    item_id, permalink = await create_listing(access_token, listing_payload)
    print(f"  Created: {item_id}")
    print(f"  Permalink: {permalink}")
    creation_time = time.time()

    # 2. Activate listing
    print(f"\n{'='*60}")
    print("STEP 2: Activating listing...")
    print(f"{'='*60}")
    await activate_listing(access_token, item_id)
    activation_time = time.time()
    print(f"  Activated in {activation_time - creation_time:.1f}s")

    # 3. Poll for candidacy every second for 30 seconds
    print(f"\n{'='*60}")
    print(f"STEP 3: Polling candidacy for {item_id} in {promo_id}...")
    print(f"{'='*60}")
    poll_seconds = 30
    async with httpx.AsyncClient(timeout=15.0) as client:
        for i in range(poll_seconds):
            elapsed = time.time() - activation_time
            resp = await client.get(
                f"{ML_API_BASE}/seller-promotions/promotions/{promo_id}/items",
                params={
                    "promotion_type": "SELLER_CAMPAIGN",
                    "app_version": "v2",
                    "status": "candidate",
                    "item_id": item_id,
                },
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results") or []
                if results:
                    print(f"\n  ** CANDIDATE FOUND at {elapsed:.1f}s! **")
                    print(f"  {json.dumps(results[0], indent=2, ensure_ascii=False)}")

                    # Also try adding it
                    print(f"\n  Attempting to add to promotion...")
                    add_resp = await client.post(
                        f"{ML_API_BASE}/seller-promotions/items/{item_id}",
                        params={"app_version": "v2"},
                        headers={**headers, "Content-Type": "application/json"},
                        json={
                            "promotion_id": promo_id,
                            "promotion_type": "SELLER_CAMPAIGN",
                            "deal_price": 120.00,
                        },
                    )
                    print(f"  Add status: {add_resp.status_code}")
                    print(f"  Response: {json.dumps(add_resp.json(), indent=2, ensure_ascii=False)}")
                    return
                else:
                    print(f"  [{elapsed:5.1f}s] Not a candidate yet...")
            else:
                print(f"  [{elapsed:5.1f}s] HTTP {resp.status_code}")

            await asyncio.sleep(1)

    total = time.time() - activation_time
    print(f"\n  Item did NOT become a candidate within {total:.0f}s")
    print(f"  The item {item_id} may need more time or may not qualify for {promo_id}")

    # Close the listing to clean up
    print(f"\n  Closing listing {item_id}...")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.put(
            f"{ML_API_BASE}/items/{item_id}",
            json={"status": "closed"},
            headers=headers,
        )
        print(f"  Close status: {resp.status_code}")


async def main():
    access_token = await get_token()

    # --check mode: check item's promotions
    if len(sys.argv) >= 3 and sys.argv[1] == "--check":
        await check_item_promotions(access_token, sys.argv[2])
        return

    # --candidacy-test mode: create listing and poll for candidacy
    if len(sys.argv) >= 2 and sys.argv[1] == "--candidacy-test":
        await candidacy_timing_test(access_token)
        return

    promos = await list_promotions_and_candidates(access_token)

    # Add item mode
    if len(sys.argv) >= 3:
        item_id = sys.argv[1]
        deal_price = float(sys.argv[2])
        await add_to_promotion(access_token, item_id, deal_price, promos)


if __name__ == "__main__":
    asyncio.run(main())
