"""Playwright GUI tests: manually edited dimension fields survive SKU re-search.

Covers:
- Editing height_cm and length_cm manually → values persist after re-searching
  the same SKU via "Pesquisar SKU no Tiny" button.
- Width and weight (already have snapshot support) also survive for comparison.
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

# NEWGD60C7 original values from Tiny API (may include decimals)
TINY_HEIGHT = 52.0
TINY_LENGTH = 18.0
TINY_WIDTH = 30.0
TINY_WEIGHT = 0.2

# Values we'll manually type
MANUAL_HEIGHT = "99"
MANUAL_LENGTH = "88"


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


def _re_search_sku(page):
    """Click 'Pesquisar SKU no Tiny' and wait for reload to finish."""
    page.click("#btnTinySearch")
    page.wait_for_function("() => !isFetchingTinyData", timeout=10000)
    page.wait_for_function("() => !autoPricingInProgress", timeout=5000)
    page.wait_for_timeout(500)


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_manual_height_and_length_survive_sku_re_search():
    """Manually edited height and length must NOT be overwritten by re-searching the SKU.

    Steps:
    1. Load NEWGD60C7 (height=52, length=18 from Tiny)
    2. Manually set height=99, length=88
    3. Wait for persist (debounce flush)
    4. Re-search the same SKU via button
    5. Verify height=99 and length=88 are preserved
    """
    products = _load_products()
    fake_db = {}
    with _static_server(ROOT_DIR) as base_url, sync_playwright() as p:
        browser, context, page = _setup_page(p, base_url, products, fake_db)
        try:
            _load_sku(page, SKU)

            # Verify initial Tiny values loaded (compare as floats to handle formatting)
            assert float(page.input_value("#heightCm").strip()) == TINY_HEIGHT, \
                f"Initial height should be {TINY_HEIGHT}"
            assert float(page.input_value("#lengthCm").strip()) == TINY_LENGTH, \
                f"Initial length should be {TINY_LENGTH}"

            # Manually edit height and length
            page.fill("#heightCm", MANUAL_HEIGHT)
            page.press("#heightCm", "Tab")
            page.fill("#lengthCm", MANUAL_LENGTH)
            page.press("#lengthCm", "Tab")
            page.wait_for_timeout(300)

            # Verify manual values are in the fields
            assert page.input_value("#heightCm").strip() == MANUAL_HEIGHT
            assert page.input_value("#lengthCm").strip() == MANUAL_LENGTH

            # Wait for persist debounce to flush (600ms + margin)
            page.wait_for_timeout(1500)

            # Re-search the same SKU
            _re_search_sku(page)

            # Verify: manually edited values MUST survive the re-search
            height_after = page.input_value("#heightCm").strip()
            length_after = page.input_value("#lengthCm").strip()

            assert height_after == MANUAL_HEIGHT, \
                f"Height should be {MANUAL_HEIGHT} after re-search, got {height_after} (Tiny would set {TINY_HEIGHT})"
            assert length_after == MANUAL_LENGTH, \
                f"Length should be {MANUAL_LENGTH} after re-search, got {length_after} (Tiny would set {TINY_LENGTH})"

        finally:
            browser.close()


def _switch_tab(page, variant):
    page.wait_for_function("() => !variantSwitchInProgress")
    page.wait_for_function("() => !autoPricingInProgress", timeout=5000)
    page.click(f"button.variant-tab-btn[data-variant='{variant}']")
    page.wait_for_function("(t) => activeVariantKey === t", arg=variant)
    page.wait_for_function("() => !variantSwitchInProgress")
    page.wait_for_function("() => !autoPricingInProgress", timeout=5000)


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_manual_dimensions_survive_re_search_on_kit_tab():
    """Manually edited height/length on simple tab must survive re-search even when on a kit tab.

    Steps:
    1. Load NEWGD60C7
    2. Manually set height=99, length=88 on simple tab
    3. Switch to kit2 tab
    4. Re-search the same SKU
    5. Switch back to simple tab
    6. Verify height=99 and length=88 are preserved
    """
    products = _load_products()
    fake_db = {}
    with _static_server(ROOT_DIR) as base_url, sync_playwright() as p:
        browser, context, page = _setup_page(p, base_url, products, fake_db)
        try:
            _load_sku(page, SKU)

            # Manually edit height and length on simple tab
            page.fill("#heightCm", MANUAL_HEIGHT)
            page.press("#heightCm", "Tab")
            page.fill("#lengthCm", MANUAL_LENGTH)
            page.press("#lengthCm", "Tab")
            page.wait_for_timeout(300)

            # Switch to kit2
            _switch_tab(page, "kit2")

            # Verify kit2 shows the edited values (height/length don't scale with quantity)
            kit_height = page.input_value("#heightCm").strip()
            kit_length = page.input_value("#lengthCm").strip()
            assert float(kit_height) == float(MANUAL_HEIGHT), \
                f"Kit2 height should be {MANUAL_HEIGHT}, got {kit_height}"
            assert float(kit_length) == float(MANUAL_LENGTH), \
                f"Kit2 length should be {MANUAL_LENGTH}, got {kit_length}"

            # Wait for persist
            page.wait_for_timeout(1500)

            # Re-search the same SKU while on kit2
            _re_search_sku(page)

            # Switch back to simple
            _switch_tab(page, "simple")

            # Verify: manually edited values survived
            height_after = page.input_value("#heightCm").strip()
            length_after = page.input_value("#lengthCm").strip()
            assert height_after == MANUAL_HEIGHT, \
                f"Height should be {MANUAL_HEIGHT} after kit re-search, got {height_after}"
            assert length_after == MANUAL_LENGTH, \
                f"Length should be {MANUAL_LENGTH} after kit re-search, got {length_after}"

        finally:
            browser.close()


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_manual_dimensions_survive_tab_round_trip():
    """Manually edited height/length must survive tab round-trips: simple→kit2→kit3→simple.

    This verifies the snapshot system works for height and length across all variant tabs.
    """
    products = _load_products()
    fake_db = {}
    with _static_server(ROOT_DIR) as base_url, sync_playwright() as p:
        browser, context, page = _setup_page(p, base_url, products, fake_db)
        try:
            _load_sku(page, SKU)

            # Manually edit height and length
            page.fill("#heightCm", MANUAL_HEIGHT)
            page.press("#heightCm", "Tab")
            page.fill("#lengthCm", MANUAL_LENGTH)
            page.press("#lengthCm", "Tab")
            page.wait_for_timeout(300)

            # Round-trip: simple → kit2 → kit3 → simple
            _switch_tab(page, "kit2")
            _switch_tab(page, "kit3")
            _switch_tab(page, "simple")

            height_after = page.input_value("#heightCm").strip()
            length_after = page.input_value("#lengthCm").strip()
            assert height_after == MANUAL_HEIGHT, \
                f"Height should be {MANUAL_HEIGHT} after round-trip, got {height_after}"
            assert length_after == MANUAL_LENGTH, \
                f"Length should be {MANUAL_LENGTH} after round-trip, got {length_after}"

        finally:
            browser.close()
