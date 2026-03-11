"""Playwright GUI tests: zero shipping cost is a valid manual input.

Covers:
- Typing 0 in shipping field locks the value (shippingCostLocked = true)
- Kit variants respect zero shipping without auto-recalculating
- Simple variant respects zero shipping without auto-recalculating
- Tab switch round-trips preserve zero shipping
- Publish validation accepts zero shipping (field is not empty)
"""
import json
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


def _set_shipping_zero(page):
    """Clear shipping field, type 0, and trigger change event."""
    page.fill("#tinyShippingCost", "0")
    page.press("#tinyShippingCost", "Tab")
    page.wait_for_timeout(300)
    page.wait_for_function("() => !autoPricingInProgress", timeout=5000)


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_kit_zero_shipping_stays_zero():
    """Typing 0 in shipping on a kit tab must NOT auto-recalculate."""
    products = _load_products()
    with _static_server(ROOT_DIR) as base_url, sync_playwright() as p:
        browser, context, page = _setup_page(p, base_url, products, {})
        try:
            _load_sku(page, SKU)

            # Switch to kit2 (which would normally auto-fill shipping)
            _switch_tab(page, "kit2")
            auto_filled = _get_shipping(page)

            # Now manually set shipping to 0
            _set_shipping_zero(page)

            # Verify it stayed 0 and shippingCostLocked is true
            assert _get_shipping(page) == "0", \
                f"Kit2 shipping should be 0 after manual input, got {_get_shipping(page)}"
            locked = page.evaluate("() => shippingCostLocked")
            assert locked is True, "shippingCostLocked should be true after typing 0"

            # Wait extra time to ensure no async auto-fill overwrites it
            page.wait_for_timeout(2000)
            assert _get_shipping(page) == "0", \
                f"Kit2 shipping was overwritten after 2s, got {_get_shipping(page)}"
        finally:
            browser.close()


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_simple_zero_shipping_stays_zero():
    """Typing 0 in shipping on simple tab must NOT auto-recalculate."""
    products = _load_products()
    with _static_server(ROOT_DIR) as base_url, sync_playwright() as p:
        browser, context, page = _setup_page(p, base_url, products, {})
        try:
            _load_sku(page, SKU)

            # Simple variant — set shipping to 0
            _set_shipping_zero(page)

            assert _get_shipping(page) == "0", \
                f"Simple shipping should be 0 after manual input, got {_get_shipping(page)}"
            locked = page.evaluate("() => shippingCostLocked")
            assert locked is True, "shippingCostLocked should be true after typing 0"

            page.wait_for_timeout(2000)
            assert _get_shipping(page) == "0", \
                f"Simple shipping was overwritten after 2s, got {_get_shipping(page)}"
        finally:
            browser.close()


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_zero_shipping_survives_tab_switch():
    """Zero shipping on kit2 must survive round-trip: kit2→kit3→kit2."""
    products = _load_products()
    with _static_server(ROOT_DIR) as base_url, sync_playwright() as p:
        browser, context, page = _setup_page(p, base_url, products, {})
        try:
            _load_sku(page, SKU)

            # Go to kit2, set shipping to 0
            _switch_tab(page, "kit2")
            _set_shipping_zero(page)
            assert _get_shipping(page) == "0"

            # Switch to kit3, then back to kit2
            _switch_tab(page, "kit3")
            _switch_tab(page, "kit2")

            val = _get_shipping(page)
            assert val == "0", \
                f"Kit2 shipping should still be 0 after tab round-trip, got {val}"
        finally:
            browser.close()


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_zero_shipping_passes_publish_validation():
    """validateWorkspaceForMlPublish must accept zero shipping (field is not empty)."""
    products = _load_products()
    with _static_server(ROOT_DIR) as base_url, sync_playwright() as p:
        browser, context, page = _setup_page(p, base_url, products, {})
        try:
            _load_sku(page, SKU)

            # Set shipping to 0
            _set_shipping_zero(page)

            # Fill required fields for publish validation
            page.evaluate("""() => {
                document.getElementById('outTitle').value = 'Test Title';
                document.getElementById('outDesc').value = 'Test Description';
            }""")

            # Wait for prices to be calculated
            page.wait_for_function(
                "() => parseFloat(document.getElementById('tinyAnnouncePriceMin')?.value || '0') > 0",
                timeout=5000,
            )

            # Run validation
            missing = page.evaluate("() => validateWorkspaceForMlPublish()")
            assert "Custo de frete" not in missing, \
                f"Validation should accept zero shipping, but got missing: {missing}"
        finally:
            browser.close()


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_empty_shipping_fails_publish_validation():
    """validateWorkspaceForMlPublish must reject empty (blank) shipping field."""
    products = _load_products()
    with _static_server(ROOT_DIR) as base_url, sync_playwright() as p:
        browser, context, page = _setup_page(p, base_url, products, {})
        try:
            _load_sku(page, SKU)

            # Clear the shipping field completely
            page.evaluate("() => { document.getElementById('tinyShippingCost').value = ''; }")

            missing = page.evaluate("() => validateWorkspaceForMlPublish()")
            assert "Custo de frete" in missing, \
                f"Validation should reject empty shipping, but missing list was: {missing}"
        finally:
            browser.close()


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_buscar_marketplace_button_unlocks_zero_shipping():
    """Clicking 'Buscar no Marketplace' should unlock shipping (allow auto-fill to override 0)."""
    products = _load_products()
    with _static_server(ROOT_DIR) as base_url, sync_playwright() as p:
        browser, context, page = _setup_page(p, base_url, products, {})
        try:
            _load_sku(page, SKU)

            # Go to kit2, set shipping to 0
            _switch_tab(page, "kit2")
            _set_shipping_zero(page)
            assert _get_shipping(page) == "0"
            assert page.evaluate("() => shippingCostLocked") is True

            # Click "Buscar no Marketplace" — should unlock and recalculate
            page.click("#btnAutoFillShipping")
            page.wait_for_timeout(1500)
            page.wait_for_function("() => !autoPricingInProgress", timeout=5000)

            locked_after = page.evaluate("() => shippingCostLocked")
            assert locked_after is False, "shippingCostLocked should be false after button click"

            val = _get_shipping(page)
            assert val != "0" and float(val) > 0, \
                f"Shipping should be auto-filled after button click, got {val}"
        finally:
            browser.close()
