"""Playwright test: 'Calcular Preços' button fills price fields correctly."""

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
except Exception:  # pragma: no cover - optional dependency
    sync_playwright = None


pytestmark = pytest.mark.skipif(sync_playwright is None, reason="Playwright nao instalado")


def _json_response(route, payload: dict, status: int = 200):
    route.fulfill(
        status=status,
        headers={"Content-Type": "application/json"},
        body=json.dumps(payload, ensure_ascii=False),
    )


@contextmanager
def _static_server(root_dir: Path):
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


MOCK_PRICING_RESPONSE = {
    "listing_price": {
        "price": 129.90,
        "metrics": {
            "margin_percent": 35.0,
            "value_multiple": 2.5,
            "value_amount": 45.00,
            "taxes": 15.60,
            "commissions": 19.48,
        },
    },
    "aggressive_price": {
        "price": 109.90,
        "metrics": {
            "margin_percent": 25.0,
            "value_multiple": 2.1,
            "value_amount": 27.00,
            "taxes": 13.20,
            "commissions": 16.48,
        },
    },
    "promo_price": {
        "price": 99.90,
        "metrics": {
            "margin_percent": 20.0,
            "value_multiple": 1.9,
            "value_amount": 19.00,
            "taxes": 12.00,
            "commissions": 14.98,
        },
    },
    "wholesale_tiers": [
        {
            "tier": 1,
            "min_quantity": 3,
            "price": 95.00,
            "metrics": {
                "margin_percent": 15.0,
                "value_multiple": 1.8,
                "value_amount": 14.00,
                "taxes": 11.40,
                "commissions": 14.25,
            },
        },
    ],
    "breakdown": {"steps": [], "notes": []},
    "channel": "mercadolivre",
    "policy_id": None,
}


def _attach_mocks(context):
    def handle_routes(route, request):
        path = urlparse(request.url).path
        method = request.method.upper()

        if path == "/api/config" and method == "GET":
            return _json_response(route, {
                "tiny_tokens": [{"label": "Tiny SP", "token": "token-sp"}],
                "pricing_config": [{
                    "marketplace": "mercadolivre",
                    "comissao_min": 12,
                    "comissao_max": 17,
                    "tacos": 5,
                    "margem_contribuicao": 15,
                    "lucro": 10,
                    "impostos": 8,
                }],
            })

        if path == "/api/sku/workspace/save" and method == "POST":
            return _json_response(route, {
                "ok": True, "saved": True,
                "workspace_id": "ws-test", "history_id": "h-1", "reason": None,
            })

        if path == "/api/sku/workspace/load" and method == "POST":
            return _json_response(
                route,
                {"detail": {"message": "SKU nao carregado", "type": "not_found"}},
                status=404,
            )

        if path == "/pricing/quote" and method == "POST":
            return _json_response(route, MOCK_PRICING_RESPONSE)

        if path == "/api/shipping/calculate_ml" and method == "POST":
            return _json_response(route, {"shipping_cost": 12.50})

        if path == "/api/canva/list" and method == "POST":
            return _json_response(route, {"design": None})

        return route.continue_()

    context.route("**/*", handle_routes)


def test_calcular_precos_button_fills_price_fields():
    """Click 'Calcular Preços' with cost filled → price fields get populated."""
    root_dir = Path(__file__).resolve().parents[2]
    with _static_server(root_dir) as base_url:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"Chromium not available: {exc}")
                return

            context = browser.new_context(viewport={"width": 1280, "height": 900})
            _attach_mocks(context)
            page = context.new_page()

            console_errors = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

            page.goto(f"{base_url}/static/main.html", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => document.querySelectorAll('#variantTabs .variant-tab-btn').length === 5"
            )
            page.wait_for_function(
                "() => document.querySelector('#tinyInstance option[value=\"0\"]') !== null"
            )
            page.select_option("#tinyInstance", "0")

            # Fill SKU so button container becomes visible
            page.fill("#tinySKU", "TEST-SKU-001")
            page.press("#tinySKU", "Enter")
            page.wait_for_timeout(500)

            # Make button visible manually (in case SKU flow didn't trigger it)
            page.evaluate("document.getElementById('manualPricingButton').style.display = 'block'")

            # Fill cost price
            page.fill("#tinyCostPrice", "50.00")

            # Ensure marketplace is selected
            marketplace_val = page.evaluate("document.getElementById('marketplace').value")
            assert marketplace_val == "mercadolivre", f"Expected mercadolivre, got {marketplace_val}"

            # Click Calcular Preços
            page.click("#calculatePricesBtn")
            page.wait_for_timeout(2000)  # Wait for async pricing calls

            # Verify price fields are populated
            announce_min = page.evaluate("document.getElementById('tinyAnnouncePriceMin')?.value || ''")
            assert announce_min != "", "tinyAnnouncePriceMin should be filled after clicking Calcular Preços"
            assert float(announce_min) == 129.90, f"Expected 129.90, got {announce_min}"

            # Verify no critical JS errors
            critical_errors = [e for e in console_errors if "TypeError" in e or "ReferenceError" in e]
            assert not critical_errors, f"JS errors found: {critical_errors}"

            browser.close()


def test_calcular_precos_shows_warning_without_cost():
    """Click 'Calcular Preços' without cost → shows warning toast."""
    root_dir = Path(__file__).resolve().parents[2]
    with _static_server(root_dir) as base_url:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"Chromium not available: {exc}")
                return

            context = browser.new_context(viewport={"width": 1280, "height": 900})
            _attach_mocks(context)
            page = context.new_page()

            page.goto(f"{base_url}/static/main.html", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => document.querySelectorAll('#variantTabs .variant-tab-btn').length === 5"
            )
            page.wait_for_function(
                "() => document.querySelector('#tinyInstance option[value=\"0\"]') !== null"
            )
            page.select_option("#tinyInstance", "0")

            # Make button visible
            page.evaluate("document.getElementById('manualPricingButton').style.display = 'block'")

            # Ensure cost is empty
            page.fill("#tinyCostPrice", "")

            # Click Calcular Preços
            page.click("#calculatePricesBtn")
            page.wait_for_timeout(1000)

            # Verify toast appeared with warning message
            toast_visible = page.evaluate("""
                () => {
                    const toasts = document.querySelectorAll('[data-toast-id]');
                    for (const t of toasts) {
                        if (t.textContent.includes('custo do produto')) return true;
                    }
                    return false;
                }
            """)
            assert toast_visible, "Warning toast should appear when cost is not filled"

            browser.close()
