"""Debug script: investigate 'Buscar no Marketplace' shipping button behavior."""
import json
import socket
import threading
from contextlib import contextmanager
from decimal import Decimal
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "tiny" / "tiny_sku_fixture.json"
ROOT_DIR = Path(__file__).resolve().parents[1]
SKU = "NEWGD60C7"

products_raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8")).get("products", {})
products = {str(k).strip().upper(): v for k, v in products_raw.items()}
fake_db = {}
shipping_calls = []


def _make_workspace(product, marketplace="mercadolivre"):
    sku = str(product.get("sku") or "").strip().upper()
    return {
        "id": f"ws-{sku.lower()}-{marketplace}", "sku": sku, "sku_normalized": sku,
        "marketplace": marketplace, "marketplace_normalized": marketplace, "state_seq": 1,
        "updated_at": "2026-03-03T18:30:00",
        "base_state": {
            "integration_mode": "tiny", "selected_marketplace": marketplace,
            "tiny_product_data": product,
            "product_fields": {
                "product_name": product.get("title", ""), "tiny_gtin": str(product.get("gtin", "")),
                "tiny_sku_display": sku,
                "tiny_height": str(product.get("height_cm", 0)),
                "tiny_width": str(product.get("width_cm", 0)),
                "tiny_length": str(product.get("length_cm", 0)),
                "tiny_weight": str(product.get("weight_kg", 0)),
                "tiny_cost_price": str(product.get("cost_price", 0)),
                "tiny_shipping_cost": str(product.get("shipping_cost", 0)),
            },
            "cost_price_cache": {}, "shipping_cost_cache": {},
        },
        "versioned_state": {"schema_version": 2, "variants": {
            k: {"title": {"versions": [], "current_index": -1}, "description": {"versions": [], "current_index": -1}, "faq_lines": [], "card_lines": []}
            for k in ["simple", "kit2", "kit3", "kit4", "kit5"]
        }},
    }


@contextmanager
def _static_server(root_dir):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root_dir), **kwargs)
        def log_message(self, fmt, *args):
            return
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        host, port = sock.getsockname()
    server = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=3)


def _json_response(route, payload, status=200):
    route.fulfill(status=status, headers={"Content-Type": "application/json"}, body=json.dumps(payload))


def handle_routes(route, request):
    path = urlparse(request.url).path
    method = request.method.upper()
    if path == "/api/config" and method == "GET":
        return _json_response(route, {
            "tiny_tokens": [{"label": "T", "token": "t"}],
            "pricing_config": [{"marketplace": "mercadolivre", "comissao_min": 12, "comissao_max": 17, "tacos": 5, "margem_contribuicao": 15, "lucro": 10, "impostos": 8}],
        })
    if path == "/api/sku/workspace/load" and method == "POST":
        body = request.post_data_json or {}
        sku = str(body.get("sku", "")).strip().upper()
        key = (sku, "mercadolivre")
        if key in fake_db:
            return _json_response(route, {"source": "db", "workspace": fake_db[key]})
        product = products.get(sku)
        if not product:
            return _json_response(route, {"detail": "not found"}, status=404)
        ws = _make_workspace(product)
        fake_db[key] = ws
        return _json_response(route, {"source": "tiny", "workspace": ws})
    if path == "/api/sku/workspace/save" and method == "POST":
        body = request.post_data_json or {}
        sku = str(body.get("sku", "")).strip().upper()
        key = (sku, "mercadolivre")
        ws = fake_db.get(key, {})
        ws["base_state"] = body.get("base_state", {})
        ws["versioned_state"] = body.get("versioned_state", {})
        ws["state_seq"] = int(ws.get("state_seq", 0)) + 1
        fake_db[key] = ws
        return _json_response(route, {"ok": True, "saved": True, "workspace_id": "x", "history_id": "h", "reason": None})
    if path == "/pricing/quote" and method == "POST":
        body = request.post_data_json or {}
        cost = float(body.get("cost_price", 0))
        shipping = float(body.get("shipping_cost", 0))
        listing = round(cost + shipping + 10, 2)
        m = {"margin_percent": 0, "value_multiple": 0, "value_amount": 0}
        return _json_response(route, {
            "listing_price": {"price": listing, "metrics": m},
            "aggressive_price": {"price": round(listing * 0.95, 2), "metrics": m},
            "promo_price": {"price": round(listing * 0.9, 2), "metrics": m},
            "wholesale_tiers": [{"min_quantity": 2, "price": round(listing * 0.92, 2), "metrics": m}],
        })
    if path == "/api/shipping/calculate_ml" and method == "POST":
        body = request.post_data_json or {}
        dc = Decimal(str(body.get("cost_price", 0)))
        if dc == Decimal("99.60"):
            sc = 19.31
        elif dc in {Decimal("149.40"), Decimal("199.20"), Decimal("249.00")}:
            sc = 22.45
        else:
            sc = round(float(dc) * 0.1, 2)
        shipping_calls.append({"cost": str(dc), "result": sc, "source": "api"})
        return _json_response(route, {"shipping_cost": sc})
    if path == "/api/canva/list":
        return _json_response(route, {"design": None})
    return route.continue_()


def read_state(page):
    return page.evaluate("""() => ({
        variant: activeVariantKey,
        cost: document.querySelector('#tinyCostPrice').value,
        shipping: document.querySelector('#tinyShippingCost').value,
        locked: shippingCostLocked,
        cache: shippingCostCache,
        snapshots: Object.fromEntries(
            Object.entries(variantStore).map(([k, v]) => [k, v.shippingCostSnapshot || ''])
        ),
        autoPricingInProgress: autoPricingInProgress,
    })""")


def switch_tab(page, variant):
    page.wait_for_function("() => !variantSwitchInProgress")
    page.wait_for_function("() => !autoPricingInProgress", timeout=5000)
    page.click(f"button.variant-tab-btn[data-variant='{variant}']")
    page.wait_for_function("(t) => activeVariantKey === t", arg=variant)
    page.wait_for_function("() => !variantSwitchInProgress")
    page.wait_for_function("() => !autoPricingInProgress", timeout=5000)


with _static_server(ROOT_DIR) as base_url:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        console_messages = []
        page.on("console", lambda msg: console_messages.append(msg.text))
        context.route("**/*", handle_routes)

        page.goto(f"{base_url}/static/main.html", wait_until="domcontentloaded")
        page.wait_for_function("() => document.querySelector('#tinyInstance option[value=\"0\"]') !== null")
        page.select_option("#tinyInstance", "0")

        page.fill("#tinySKU", SKU)
        page.press("#tinySKU", "Enter")
        page.wait_for_function("(t) => document.querySelector('#tinySKUDisplay').value.toUpperCase().includes(t)", arg=SKU)
        page.wait_for_function("() => (document.querySelector('#tinyCostPrice').value || '').trim().length > 0")
        page.wait_for_timeout(500)  # Wait for initial autoPricing

        # ═══════════════════════════════════════════════════════════════════
        # TEST 1: Simple tab — button click with NO shipping set
        # ═══════════════════════════════════════════════════════════════════
        print("=" * 60)
        print("TEST 1: Simple tab — button click (no shipping)")
        state_before = read_state(page)
        print(f"  BEFORE: shipping={state_before['shipping']} locked={state_before['locked']}")

        shipping_calls.clear()
        page.click("#btnAutoFillShipping")
        page.wait_for_timeout(1500)  # Wait for async fetch
        page.wait_for_function("() => !autoPricingInProgress", timeout=5000)

        state_after = read_state(page)
        print(f"  AFTER:  shipping={state_after['shipping']} locked={state_after['locked']}")
        print(f"  snapshot(simple)={state_after['snapshots']['simple']}")
        print(f"  cache={json.dumps(state_after['cache'])}")
        print(f"  API calls: {shipping_calls}")
        changed = state_before['shipping'] != state_after['shipping']
        print(f"  VALUE CHANGED: {changed}")
        snapshot_synced = state_after['snapshots']['simple'] == state_after['shipping']
        print(f"  SNAPSHOT SYNCED: {snapshot_synced}")

        # ═══════════════════════════════════════════════════════════════════
        # TEST 2: Simple tab — manually set shipping, then click button
        # ═══════════════════════════════════════════════════════════════════
        print("\n" + "=" * 60)
        print("TEST 2: Simple tab — manual shipping, then button click")

        # Manually set shipping to 50.00
        page.fill("#tinyShippingCost", "50.00")
        page.press("#tinyShippingCost", "Tab")  # Trigger change event
        page.wait_for_timeout(500)

        state_before = read_state(page)
        print(f"  AFTER MANUAL SET: shipping={state_before['shipping']} locked={state_before['locked']}")
        print(f"  snapshot(simple)={state_before['snapshots']['simple']}")

        shipping_calls.clear()
        page.click("#btnAutoFillShipping")
        page.wait_for_timeout(1500)
        page.wait_for_function("() => !autoPricingInProgress", timeout=5000)

        state_after = read_state(page)
        print(f"  AFTER BUTTON: shipping={state_after['shipping']} locked={state_after['locked']}")
        print(f"  snapshot(simple)={state_after['snapshots']['simple']}")
        print(f"  cache={json.dumps(state_after['cache'])}")
        print(f"  API calls: {shipping_calls}")
        changed = state_before['shipping'] != state_after['shipping']
        print(f"  VALUE CHANGED: {changed}")
        snapshot_synced = state_after['snapshots']['simple'] == state_after['shipping']
        print(f"  SNAPSHOT SYNCED: {snapshot_synced}")

        # ═══════════════════════════════════════════════════════════════════
        # TEST 3: Switch to kit2, manually set, button click
        # ═══════════════════════════════════════════════════════════════════
        print("\n" + "=" * 60)
        print("TEST 3: Kit2 — auto-filled shipping, manual override, then button")
        switch_tab(page, "kit2")

        state_kit2_initial = read_state(page)
        print(f"  INITIAL: shipping={state_kit2_initial['shipping']} locked={state_kit2_initial['locked']}")

        # Manually override kit2 shipping
        page.fill("#tinyShippingCost", "77.77")
        page.press("#tinyShippingCost", "Tab")
        page.wait_for_timeout(500)

        state_before = read_state(page)
        print(f"  AFTER MANUAL: shipping={state_before['shipping']} locked={state_before['locked']}")

        shipping_calls.clear()
        page.click("#btnAutoFillShipping")
        page.wait_for_timeout(1500)
        page.wait_for_function("() => !autoPricingInProgress", timeout=5000)

        state_after = read_state(page)
        print(f"  AFTER BUTTON: shipping={state_after['shipping']} locked={state_after['locked']}")
        print(f"  snapshot(kit2)={state_after['snapshots']['kit2']}")
        print(f"  API calls: {shipping_calls}")
        changed = state_before['shipping'] != state_after['shipping']
        print(f"  VALUE CHANGED: {changed}")
        snapshot_synced = state_after['snapshots']['kit2'] == state_after['shipping']
        print(f"  SNAPSHOT SYNCED: {snapshot_synced}")

        # ═══════════════════════════════════════════════════════════════════
        # TEST 4: Tab switch round-trip after button click
        # ═══════════════════════════════════════════════════════════════════
        print("\n" + "=" * 60)
        print("TEST 4: Tab switch round-trip preservation")

        # Record current state
        kit2_shipping = state_after['shipping']
        simple_state = read_state(page)

        # Switch to kit3, then back to kit2
        switch_tab(page, "kit3")
        kit3_state = read_state(page)
        print(f"  kit3: shipping={kit3_state['shipping']}")

        switch_tab(page, "kit2")
        kit2_back = read_state(page)
        print(f"  kit2 (back): shipping={kit2_back['shipping']}")
        print(f"  kit2 preserved: {kit2_back['shipping'] == kit2_shipping}")

        # Switch to simple
        switch_tab(page, "simple")
        simple_back = read_state(page)
        print(f"  simple (back): shipping={simple_back['shipping']}")

        # ═══════════════════════════════════════════════════════════════════
        # TEST 5: Does button trigger autoPricing?
        # ═══════════════════════════════════════════════════════════════════
        print("\n" + "=" * 60)
        print("TEST 5: Button -> autoPricing trigger check")

        # Get current prices
        prices_before = page.evaluate("""() => ({
            announceMin: document.querySelector('#tinyAnnouncePriceMin')?.value || '',
            announceMax: document.querySelector('#tinyAnnouncePriceMax')?.value || '',
        })""")
        print(f"  prices BEFORE button: min={prices_before['announceMin']} max={prices_before['announceMax']}")

        # Manually set a very different shipping to make price change obvious
        page.fill("#tinyShippingCost", "99.99")
        page.press("#tinyShippingCost", "Tab")
        page.wait_for_timeout(1000)
        page.wait_for_function("() => !autoPricingInProgress", timeout=5000)

        prices_after_manual = page.evaluate("""() => ({
            announceMin: document.querySelector('#tinyAnnouncePriceMin')?.value || '',
            announceMax: document.querySelector('#tinyAnnouncePriceMax')?.value || '',
        })""")
        print(f"  prices AFTER manual edit: min={prices_after_manual['announceMin']} max={prices_after_manual['announceMax']}")

        # Now click button to reset shipping
        shipping_calls.clear()
        page.click("#btnAutoFillShipping")
        page.wait_for_timeout(2000)

        # Check if autoPricing is even triggered
        auto_pricing_ran = page.evaluate("() => !autoPricingInProgress")
        prices_after_button = page.evaluate("""() => ({
            announceMin: document.querySelector('#tinyAnnouncePriceMin')?.value || '',
            announceMax: document.querySelector('#tinyAnnouncePriceMax')?.value || '',
            shipping: document.querySelector('#tinyShippingCost').value,
        })""")
        print(f"  prices AFTER button: min={prices_after_button['announceMin']} max={prices_after_button['announceMax']}")
        print(f"  shipping AFTER button: {prices_after_button['shipping']}")
        prices_changed = prices_after_manual['announceMin'] != prices_after_button['announceMin']
        print(f"  PRICES RECALCULATED: {prices_changed}")
        print(f"  API calls: {shipping_calls}")

        # ═══════════════════════════════════════════════════════════════════
        # TEST 6: decisionCostBase comparison
        # ═══════════════════════════════════════════════════════════════════
        print("\n" + "=" * 60)
        print("TEST 6: Button cost_price vs autoPricing cost_price")

        switch_tab(page, "kit3")
        page.wait_for_timeout(500)

        kit3_cost = page.evaluate("() => document.querySelector('#tinyCostPrice').value")
        decision_base = page.evaluate("() => getShippingDecisionBaseCost(parseFloat(document.querySelector('#tinyCostPrice').value))")
        print(f"  kit3 tinyCostPrice: {kit3_cost}")
        print(f"  kit3 getShippingDecisionBaseCost: {decision_base}")

        shipping_calls.clear()
        page.click("#btnAutoFillShipping")
        page.wait_for_timeout(1500)
        button_calls = list(shipping_calls)
        print(f"  Button API call cost_price: {button_calls}")

        # Now trigger autoPricing by editing shipping and tabbing
        shipping_calls.clear()
        page.fill("#tinyShippingCost", "")
        page.press("#tinyShippingCost", "Tab")
        page.wait_for_timeout(1500)
        page.wait_for_function("() => !autoPricingInProgress", timeout=5000)
        auto_calls = list(shipping_calls)
        print(f"  autoPricing API call cost_price: {auto_calls}")

        if button_calls and auto_calls:
            same_cost = button_calls[0]['cost'] == auto_calls[0]['cost']
            print(f"  SAME cost_price: {same_cost}")
        else:
            print(f"  Could not compare (button_calls={len(button_calls)}, auto_calls={len(auto_calls)})")

        # ═══════════════════════════════════════════════════════════════════
        # SUMMARY
        # ═══════════════════════════════════════════════════════════════════
        print("\n" + "=" * 60)
        print("ALL SHIPPING API CALLS (chronological):")
        # Reset and show all
        print(f"  Total API calls made: {len(shipping_calls)}")

        print("\nCONSOLE WARNINGS/ERRORS:")
        for msg in console_messages:
            if "error" in msg.lower() or "warn" in msg.lower() or "ATENCAO" in msg:
                print(f"  {msg[:200]}")

        browser.close()
