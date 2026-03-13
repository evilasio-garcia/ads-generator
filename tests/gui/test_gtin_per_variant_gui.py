"""Playwright GUI tests: GTIN/EAN per-variant editing and persistence.

Covers:
- GTIN editable on kit tabs (not readOnly).
- Each kit variant has its own GTIN that survives tab switches.
- Simple variant GTIN survives re-search (snapshot priority over Tiny).
- Kit GTIN starts empty when first opened, stays editable.
"""
import json
import socket
import threading
from contextlib import contextmanager
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

# NEWGD60C7 original GTIN from Tiny API
TINY_GTIN = "7898590070977"

# Manual GTIN values for testing
MANUAL_GTIN_SIMPLE = "1111111111111"
MANUAL_GTIN_KIT2 = "2222222222222"
MANUAL_GTIN_KIT3 = "3333333333333"


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
                "height_cm": str(product.get("height_cm", 0)),
                "width_cm": str(product.get("width_cm", 0)),
                "length_cm": str(product.get("length_cm", 0)),
                "weight_kg": str(product.get("weight_kg", 0)),
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


def _build_route_handler(products, fake_db):
    """Route handler that intercepts API calls and returns fake responses."""
    def handle_routes(route, request):
        path = urlparse(request.url).path
        method = request.method.upper()
        if path == "/api/config" and method == "GET":
            return _json_response(route, {
                "tiny_tokens": [{"label": "T", "token": "t"}],
                "pricing_config": [{"marketplace": "mercadolivre", "comissao_min": 12, "comissao_max": 17, "tacos": 5, "margem_contribuicao": 15, "lucro": 10, "impostos": 8}],
                "ml_accounts": [{"ml_user_id": "999", "nickname": "TestAccount"}],
                "ml_category_mappings": [{"ml_category_id": "MLB12345", "ml_user_id": "999", "adsgen_name": "Test", "ml_category_name": "Test"}],
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
            return _json_response(route, {"shipping_cost": 0})
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


def _re_search_sku(page):
    """Click 'Pesquisar SKU no Tiny' and wait for reload to finish."""
    page.click("#btnTinySearch")
    page.wait_for_function("() => !isFetchingTinyData", timeout=10000)
    page.wait_for_function("() => !autoPricingInProgress", timeout=5000)
    page.wait_for_timeout(500)


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_gtin_editable_on_kit_tab():
    """GTIN field must NOT be readOnly on kit tabs."""
    products = _load_products()
    fake_db = {}
    with _static_server(ROOT_DIR) as base_url, sync_playwright() as p:
        browser, context, page = _setup_page(p, base_url, products, fake_db)
        try:
            _load_sku(page, SKU)

            # Verify GTIN loaded from Tiny on simple tab
            gtin_val = page.input_value("#tinyGTIN").strip()
            assert gtin_val == TINY_GTIN, f"Expected GTIN {TINY_GTIN}, got {gtin_val}"

            # Verify GTIN is NOT readOnly on simple
            assert not page.evaluate("document.getElementById('tinyGTIN').readOnly"), \
                "GTIN should be editable on simple tab"

            # Switch to kit2
            _switch_tab(page, "kit2")

            # Verify GTIN is NOT readOnly on kit tab
            assert not page.evaluate("document.getElementById('tinyGTIN').readOnly"), \
                "GTIN should be editable on kit2 tab"

        finally:
            browser.close()


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_kit_gtin_starts_empty():
    """When switching to a kit tab for the first time, GTIN should be empty."""
    products = _load_products()
    fake_db = {}
    with _static_server(ROOT_DIR) as base_url, sync_playwright() as p:
        browser, context, page = _setup_page(p, base_url, products, fake_db)
        try:
            _load_sku(page, SKU)

            # Simple has Tiny GTIN
            assert page.input_value("#tinyGTIN").strip() == TINY_GTIN

            # Switch to kit2 — first time, should be empty
            _switch_tab(page, "kit2")
            kit2_gtin = page.input_value("#tinyGTIN").strip()
            assert kit2_gtin == "", f"Kit2 GTIN should be empty on first visit, got '{kit2_gtin}'"

        finally:
            browser.close()


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_each_kit_has_own_gtin():
    """Each kit variant must have its own independent GTIN value."""
    products = _load_products()
    fake_db = {}
    with _static_server(ROOT_DIR) as base_url, sync_playwright() as p:
        browser, context, page = _setup_page(p, base_url, products, fake_db)
        try:
            _load_sku(page, SKU)

            # Set kit2 GTIN
            _switch_tab(page, "kit2")
            page.fill("#tinyGTIN", MANUAL_GTIN_KIT2)
            page.press("#tinyGTIN", "Tab")
            page.wait_for_timeout(300)

            # Set kit3 GTIN
            _switch_tab(page, "kit3")
            page.fill("#tinyGTIN", MANUAL_GTIN_KIT3)
            page.press("#tinyGTIN", "Tab")
            page.wait_for_timeout(300)

            # Verify kit2 still has its own GTIN
            _switch_tab(page, "kit2")
            assert page.input_value("#tinyGTIN").strip() == MANUAL_GTIN_KIT2, \
                f"Kit2 GTIN should be {MANUAL_GTIN_KIT2}"

            # Verify kit3 still has its own GTIN
            _switch_tab(page, "kit3")
            assert page.input_value("#tinyGTIN").strip() == MANUAL_GTIN_KIT3, \
                f"Kit3 GTIN should be {MANUAL_GTIN_KIT3}"

            # Verify simple still has original GTIN
            _switch_tab(page, "simple")
            assert page.input_value("#tinyGTIN").strip() == TINY_GTIN, \
                f"Simple GTIN should be {TINY_GTIN}"

        finally:
            browser.close()


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_simple_gtin_survives_re_search():
    """Manually edited GTIN on simple tab must survive SKU re-search."""
    products = _load_products()
    fake_db = {}
    with _static_server(ROOT_DIR) as base_url, sync_playwright() as p:
        browser, context, page = _setup_page(p, base_url, products, fake_db)
        try:
            _load_sku(page, SKU)

            # Edit GTIN on simple
            page.fill("#tinyGTIN", MANUAL_GTIN_SIMPLE)
            page.press("#tinyGTIN", "Tab")
            page.wait_for_timeout(1500)  # Wait for persist debounce

            # Re-search
            _re_search_sku(page)

            # Verify manually edited GTIN survived
            gtin_after = page.input_value("#tinyGTIN").strip()
            assert gtin_after == MANUAL_GTIN_SIMPLE, \
                f"Simple GTIN should be {MANUAL_GTIN_SIMPLE} after re-search, got {gtin_after}"

        finally:
            browser.close()


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_kit_gtin_survives_tab_round_trip():
    """Kit GTIN must survive tab round-trips: kit2→kit3→simple→kit2."""
    products = _load_products()
    fake_db = {}
    with _static_server(ROOT_DIR) as base_url, sync_playwright() as p:
        browser, context, page = _setup_page(p, base_url, products, fake_db)
        try:
            _load_sku(page, SKU)

            # Set GTIN on kit2
            _switch_tab(page, "kit2")
            page.fill("#tinyGTIN", MANUAL_GTIN_KIT2)
            page.press("#tinyGTIN", "Tab")
            page.wait_for_timeout(300)

            # Round-trip: kit2 → kit3 → simple → kit2
            _switch_tab(page, "kit3")
            _switch_tab(page, "simple")
            _switch_tab(page, "kit2")

            gtin_after = page.input_value("#tinyGTIN").strip()
            assert gtin_after == MANUAL_GTIN_KIT2, \
                f"Kit2 GTIN should be {MANUAL_GTIN_KIT2} after round-trip, got {gtin_after}"

        finally:
            browser.close()
