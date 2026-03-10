"""Debug script: shipping cost across variant tabs using Playwright."""
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
        shipping_calls.append({"cost": str(dc), "result": sc})
        return _json_response(route, {"shipping_cost": sc})
    if path == "/api/canva/list":
        return _json_response(route, {"design": None})
    return route.continue_()


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

        # Instrument: trace writes to kit3.shippingCostSnapshot + autoPricing calls
        page.evaluate("""() => {
            const kit3 = variantStore.kit3;
            let _shipSnap = kit3.shippingCostSnapshot || "";
            Object.defineProperty(kit3, 'shippingCostSnapshot', {
                get() { return _shipSnap; },
                set(v) {
                    if (v !== _shipSnap) {
                        console.log(`[TRACE kit3.shippingCostSnapshot] ${JSON.stringify(_shipSnap)} -> ${JSON.stringify(v)} | activeVariant=${activeVariantKey} | field=${document.querySelector('#tinyShippingCost').value}`);
                    }
                    _shipSnap = v;
                },
                enumerable: true,
                configurable: true,
            });

            // Patch autoPricing to log entry
            const origAutoPricing = window.autoPricing || (() => {});
            // Can't easily patch, just log from switchVariantTab
        }""")

        def sw(v):
            page.wait_for_function("() => !variantSwitchInProgress")
            before = page.evaluate("""() => ({
                variant: activeVariantKey,
                shipping: document.querySelector('#tinyShippingCost').value,
                autoPricingInProgress: autoPricingInProgress,
            })""")
            print(f"  [before {v}] variant={before['variant']} shipping={before['shipping']} autoPricingInProgress={before['autoPricingInProgress']}")
            page.click(f"button.variant-tab-btn[data-variant='{v}']")
            page.wait_for_function("(t) => activeVariantKey === t", arg=v)
            page.wait_for_function("() => !variantSwitchInProgress")
            page.wait_for_function("() => !autoPricingInProgress", timeout=5000)
            # Check shipping RIGHT after hydration
            after = page.evaluate("""() => ({
                shipping: document.querySelector('#tinyShippingCost').value,
                snapshot: variantStore[activeVariantKey].shippingCostSnapshot,
            })""")
            print(f"  [after  {v}] shipping={after['shipping']} snapshot={after['snapshot']}")
            page.wait_for_timeout(200)

        def read():
            return page.evaluate("""() => ({
                variant: activeVariantKey,
                cost: document.querySelector('#tinyCostPrice').value,
                shipping: document.querySelector('#tinyShippingCost').value,
                locked: shippingCostLocked,
                cache: shippingCostCache,
                snapshots: Object.fromEntries(
                    Object.entries(variantStore).map(([k, v]) => [k, v.shippingCostSnapshot || ''])
                ),
            })""")

        print("== simple ==")
        page.wait_for_timeout(500)
        print(json.dumps(read(), indent=2))

        for vk in ["kit2", "kit3", "kit4", "kit5"]:
            sw(vk)
            print(f"\n== {vk} ==")
            print(json.dumps(read(), indent=2))

        print("\n== shipping API calls ==")
        for c in shipping_calls:
            print(f"  cost={c['cost']} => {c['result']}")

        print("\n== console logs ==")
        for msg in console_messages:
            if "[hydrate" in msg or "[TRACE" in msg:
                print(f"  {msg}")

        browser.close()
