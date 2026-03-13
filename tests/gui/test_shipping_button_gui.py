"""Playwright GUI tests for the 'Buscar no Marketplace' shipping button.

Covers:
- Button resets manually entered shipping to auto-calculated value
- Button updates snapshot, cache, and triggers autoPricing
- Tab switch round-trips preserve button-refreshed shipping
- F5 reload preserves button-refreshed shipping across all variants
- Random tab switch sequences don't lose shipping values
"""
import json
import random
import socket
import threading
from contextlib import contextmanager
from decimal import Decimal
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "tiny" / "tiny_sku_fixture.json"
ROOT_DIR = Path(__file__).resolve().parents[2]
SKU = "NEWGD60C7"

ALL_VARIANTS = ["simple", "kit2", "kit3", "kit4", "kit5"]

# Expected shipping per variant (from reference fixture; decision_cost = cost * 2)
EXPECTED_SHIPPING = {
    "simple": "0.00",   # decision_cost = 49.80 -> no auto-fill (cost < 78.99)
    "kit2": "19.31",    # decision_cost = 99.60
    "kit3": "22.45",    # decision_cost = 149.40
    "kit4": "22.45",    # decision_cost = 199.20
    "kit5": "22.45",    # decision_cost = 249.00
}

# Expected shipping from button click (uses getShippingDecisionBaseCost)
# Simple: cost=24.90, decisionBase=49.80 -> below threshold (<=78.99) -> 0.00
# kit2: cost=49.80, decisionBase=99.60 -> 19.31
# kit3: cost=74.70, decisionBase=149.40 -> 22.45
# kit4: cost=99.60, decisionBase=199.20 -> 22.45
# kit5: cost=124.50, decisionBase=249.00 -> 22.45
BUTTON_SHIPPING = {
    "simple": "0.00",
    "kit2": "19.31",
    "kit3": "22.45",
    "kit4": "22.45",
    "kit5": "22.45",
}


def _load_products():
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    products = payload.get("products", {})
    return {str(k).strip().upper(): v for k, v in products.items()}


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
            for k in ALL_VARIANTS
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


def _shipping_for_decision_base(dc):
    if dc == Decimal("99.60"):
        return 19.31
    if dc in {Decimal("149.40"), Decimal("199.20"), Decimal("249.00")}:
        return 22.45
    return round(float(dc) * 0.1, 2)


def _build_route_handler(products, fake_db):
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
            sc = _shipping_for_decision_base(dc)
            return _json_response(route, {"shipping_cost": sc})
        if path == "/api/canva/list":
            return _json_response(route, {"design": None})
        return route.continue_()
    return handle_routes


def _setup_page(p, base_url, products, fake_db):
    try:
        browser = p.chromium.launch(headless=True)
    except Exception as exc:
        pytest.skip(f"Chromium indisponivel: {exc}")
    context = browser.new_context()
    page = context.new_page()
    context.route("**/*", _build_route_handler(products, fake_db))
    page.goto(f"{base_url}/static/main.html", wait_until="domcontentloaded")
    page.wait_for_function("() => document.querySelector('#tinyInstance option[value=\"0\"]') !== null")
    page.select_option("#tinyInstance", "0")
    return browser, context, page


def _load_sku(page, sku):
    page.fill("#tinySKU", sku)
    page.press("#tinySKU", "Enter")
    page.wait_for_function(
        "(t) => document.querySelector('#tinySKUDisplay').value.toUpperCase().includes(t)",
        arg=sku,
    )
    page.wait_for_function("() => (document.querySelector('#tinyCostPrice').value || '').trim().length > 0")
    page.wait_for_timeout(500)


def _switch_tab(page, variant):
    page.wait_for_function("() => !variantSwitchInProgress")
    page.wait_for_function("() => !autoPricingInProgress", timeout=5000)
    page.click(f"button.variant-tab-btn[data-variant='{variant}']")
    page.wait_for_function("(t) => activeVariantKey === t", arg=variant)
    page.wait_for_function("() => !variantSwitchInProgress")
    page.wait_for_function("() => !autoPricingInProgress", timeout=5000)


def _get_shipping(page):
    return page.input_value("#tinyShippingCost").strip()


def _click_shipping_button(page):
    page.click("#btnAutoFillShipping")
    page.wait_for_timeout(1500)
    page.wait_for_function("() => !autoPricingInProgress", timeout=5000)


def _get_prices(page):
    return page.evaluate("""() => ({
        announceMin: document.querySelector('#tinyAnnouncePriceMin')?.value || '',
        announceMax: document.querySelector('#tinyAnnouncePriceMax')?.value || '',
    })""")


def _get_snapshots(page):
    return page.evaluate("""() => Object.fromEntries(
        Object.entries(variantStore).map(([k, v]) => [k, v.shippingCostSnapshot || ''])
    )""")


# ============================================================================
# Test 1: Button resets manual shipping to auto-calculated value
# ============================================================================
@pytest.mark.skipif(sync_playwright is None, reason="playwright nao instalado")
def test_button_resets_manual_shipping_on_simple_and_kits():
    products = _load_products()
    fake_db = {}
    with _static_server(ROOT_DIR) as base_url:
        with sync_playwright() as p:
            browser, context, page = _setup_page(p, base_url, products, fake_db)
            _load_sku(page, SKU)

            # Simple tab: manually set shipping, then button
            page.fill("#tinyShippingCost", "50.00")
            page.press("#tinyShippingCost", "Tab")
            page.wait_for_timeout(300)
            assert _get_shipping(page) == "50.00", "manual shipping not applied"

            _click_shipping_button(page)
            got = _get_shipping(page)
            assert got == BUTTON_SHIPPING["simple"], (
                f"simple: button should reset to {BUTTON_SHIPPING['simple']}, got {got}"
            )

            # Kit2: manually set shipping, then button
            _switch_tab(page, "kit2")
            page.fill("#tinyShippingCost", "88.88")
            page.press("#tinyShippingCost", "Tab")
            page.wait_for_timeout(300)
            assert _get_shipping(page) == "88.88"

            _click_shipping_button(page)
            got = _get_shipping(page)
            assert got == BUTTON_SHIPPING["kit2"], (
                f"kit2: button should reset to {BUTTON_SHIPPING['kit2']}, got {got}"
            )

            # Kit4: manually set shipping, then button
            _switch_tab(page, "kit4")
            page.fill("#tinyShippingCost", "11.11")
            page.press("#tinyShippingCost", "Tab")
            page.wait_for_timeout(300)

            _click_shipping_button(page)
            got = _get_shipping(page)
            assert got == BUTTON_SHIPPING["kit4"], (
                f"kit4: button should reset to {BUTTON_SHIPPING['kit4']}, got {got}"
            )

            browser.close()


# ============================================================================
# Test 2: Button triggers autoPricing (prices recalculated)
# ============================================================================
@pytest.mark.skipif(sync_playwright is None, reason="playwright nao instalado")
def test_button_triggers_auto_pricing():
    products = _load_products()
    fake_db = {}
    with _static_server(ROOT_DIR) as base_url:
        with sync_playwright() as p:
            browser, context, page = _setup_page(p, base_url, products, fake_db)
            _load_sku(page, SKU)

            # Record prices with auto-calculated shipping
            prices_auto = _get_prices(page)

            # Manually set very different shipping
            page.fill("#tinyShippingCost", "99.99")
            page.press("#tinyShippingCost", "Tab")
            page.wait_for_timeout(500)
            page.wait_for_function("() => !autoPricingInProgress", timeout=5000)

            prices_manual = _get_prices(page)
            assert prices_manual["announceMin"] != prices_auto["announceMin"], (
                "prices should change after manual shipping edit"
            )

            # Click button to reset
            _click_shipping_button(page)
            prices_after_button = _get_prices(page)

            # Prices should be recalculated (not same as manual)
            assert prices_after_button["announceMin"] != prices_manual["announceMin"], (
                "button should trigger autoPricing and change prices"
            )

            browser.close()


# ============================================================================
# Test 3: Button updates snapshot and cache correctly
# ============================================================================
@pytest.mark.skipif(sync_playwright is None, reason="playwright nao instalado")
def test_button_updates_snapshot_and_cache():
    products = _load_products()
    fake_db = {}
    with _static_server(ROOT_DIR) as base_url:
        with sync_playwright() as p:
            browser, context, page = _setup_page(p, base_url, products, fake_db)
            _load_sku(page, SKU)

            # Kit3: click button
            _switch_tab(page, "kit3")
            page.fill("#tinyShippingCost", "55.55")
            page.press("#tinyShippingCost", "Tab")
            page.wait_for_timeout(300)

            _click_shipping_button(page)

            # Check snapshot is synced
            snapshots = _get_snapshots(page)
            field_val = _get_shipping(page)
            assert snapshots["kit3"] == field_val, (
                f"kit3 snapshot ({snapshots['kit3']}) should match field ({field_val})"
            )

            # Check cache is populated
            cache = page.evaluate("() => shippingCostCache")
            cache_key = f"{SKU}:mercadolivre:kit3"
            assert cache_key in cache, f"cache should contain {cache_key}"
            assert str(cache[cache_key]) == field_val or f"{cache[cache_key]:.2f}" == field_val

            browser.close()


# ============================================================================
# Test 4: Tab switch round-trips preserve button-refreshed shipping
# ============================================================================
@pytest.mark.skipif(sync_playwright is None, reason="playwright nao instalado")
def test_tab_switch_preserves_button_refreshed_shipping():
    products = _load_products()
    fake_db = {}
    with _static_server(ROOT_DIR) as base_url:
        with sync_playwright() as p:
            browser, context, page = _setup_page(p, base_url, products, fake_db)
            _load_sku(page, SKU)

            # Set manual shipping on simple, then button-refresh
            page.fill("#tinyShippingCost", "50.00")
            page.press("#tinyShippingCost", "Tab")
            page.wait_for_timeout(300)
            _click_shipping_button(page)
            simple_shipping = _get_shipping(page)

            # Kit2: button-refresh
            _switch_tab(page, "kit2")
            page.fill("#tinyShippingCost", "33.33")
            page.press("#tinyShippingCost", "Tab")
            page.wait_for_timeout(300)
            _click_shipping_button(page)
            kit2_shipping = _get_shipping(page)

            # Kit4: button-refresh
            _switch_tab(page, "kit4")
            _click_shipping_button(page)
            kit4_shipping = _get_shipping(page)

            # Round-trip: kit4 -> simple -> kit2 -> kit4
            _switch_tab(page, "simple")
            assert _get_shipping(page) == simple_shipping, "simple shipping lost after round-trip"

            _switch_tab(page, "kit2")
            assert _get_shipping(page) == kit2_shipping, "kit2 shipping lost after round-trip"

            _switch_tab(page, "kit4")
            assert _get_shipping(page) == kit4_shipping, "kit4 shipping lost after round-trip"

            browser.close()


# ============================================================================
# Test 5: Random tab switch sequence doesn't lose shipping values
# ============================================================================
@pytest.mark.skipif(sync_playwright is None, reason="playwright nao instalado")
def test_random_tab_switches_preserve_shipping():
    products = _load_products()
    fake_db = {}
    with _static_server(ROOT_DIR) as base_url:
        with sync_playwright() as p:
            browser, context, page = _setup_page(p, base_url, products, fake_db)
            _load_sku(page, SKU)

            # Click button on simple to establish baseline
            _click_shipping_button(page)
            expected = {"simple": _get_shipping(page)}

            # Navigate through all kits and click button on each
            for variant in ["kit2", "kit3", "kit4", "kit5"]:
                _switch_tab(page, variant)
                _click_shipping_button(page)
                expected[variant] = _get_shipping(page)

            # Random sequence of 15 tab switches
            rng = random.Random(42)  # deterministic seed
            sequence = [rng.choice(ALL_VARIANTS) for _ in range(15)]

            for variant in sequence:
                _switch_tab(page, variant)
                got = _get_shipping(page)
                assert got == expected[variant], (
                    f"after random switch to {variant}: expected {expected[variant]}, got {got}"
                )

            browser.close()


# ============================================================================
# Test 6: F5 reload preserves button-refreshed shipping
# ============================================================================
@pytest.mark.skipif(sync_playwright is None, reason="playwright nao instalado")
def test_f5_reload_preserves_button_refreshed_shipping():
    products = _load_products()
    fake_db = {}
    with _static_server(ROOT_DIR) as base_url:
        with sync_playwright() as p:
            browser, context, page = _setup_page(p, base_url, products, fake_db)
            _load_sku(page, SKU)

            # Set button-refreshed shipping on all variants
            expected = {}

            _click_shipping_button(page)
            expected["simple"] = _get_shipping(page)

            for variant in ["kit2", "kit3", "kit4", "kit5"]:
                _switch_tab(page, variant)
                _click_shipping_button(page)
                expected[variant] = _get_shipping(page)

            # Wait for persist to flush (600ms debounce + margin)
            page.wait_for_timeout(1500)

            # F5 reload
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("() => document.querySelector('#tinyInstance option[value=\"0\"]') !== null")
            page.select_option("#tinyInstance", "0")

            # Reload SKU
            _load_sku(page, SKU)

            # Check simple -> kit2 -> kit3 -> kit4 -> kit5
            for variant in ALL_VARIANTS:
                if variant != "simple":
                    _switch_tab(page, variant)
                got = _get_shipping(page)
                assert got == expected[variant], (
                    f"after F5, {variant}: expected {expected[variant]}, got {got}"
                )

            # Check reverse: kit5 -> kit4 -> kit3 -> kit2 -> simple
            for variant in reversed(ALL_VARIANTS):
                _switch_tab(page, variant)
                got = _get_shipping(page)
                assert got == expected[variant], (
                    f"after F5 reverse, {variant}: expected {expected[variant]}, got {got}"
                )

            browser.close()


# ============================================================================
# Test 7: Button unlocks shippingCostLocked
# ============================================================================
@pytest.mark.skipif(sync_playwright is None, reason="playwright nao instalado")
def test_button_unlocks_shipping_cost_locked():
    products = _load_products()
    fake_db = {}
    with _static_server(ROOT_DIR) as base_url:
        with sync_playwright() as p:
            browser, context, page = _setup_page(p, base_url, products, fake_db)
            _load_sku(page, SKU)

            # Manually set shipping -> locks
            page.fill("#tinyShippingCost", "50.00")
            page.press("#tinyShippingCost", "Tab")
            page.wait_for_timeout(300)
            locked = page.evaluate("() => shippingCostLocked")
            assert locked is True, "manual edit should lock shipping"

            # Click button -> unlocks
            _click_shipping_button(page)
            locked = page.evaluate("() => shippingCostLocked")
            assert locked is False, "button should unlock shipping"

            browser.close()
