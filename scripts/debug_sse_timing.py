"""Debug script: test SSE event delivery timing from server to browser."""
import asyncio
import json
import socket
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parents[1]

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import uvicorn

sse_app = FastAPI()
sse_events = []


@sse_app.get("/api/ml/publish/{job_id}/events")
async def sse_endpoint(job_id: str):
    async def generator():
        sent = 0
        for _ in range(600):  # max 60s
            while sent < len(sse_events):
                evt = sse_events[sent]
                yield f"data: {json.dumps(evt)}\n\n"
                step = evt.get("step")
                sent += 1
                if step in ("done", "error"):
                    return
                # Force TCP flush between batched events
                if sent < len(sse_events):
                    await asyncio.sleep(0.05)
            await asyncio.sleep(0.15)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_sse_server(port):
    uvicorn.run(sse_app, host="127.0.0.1", port=port, log_level="warning")


def main():
    sse_port = _find_free_port()
    sse_thread = threading.Thread(target=_run_sse_server, args=(sse_port,), daemon=True)
    sse_thread.start()
    time.sleep(1)

    static_port = _find_free_port()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(ROOT_DIR), **kw)
        def log_message(self, *a):
            return

    static_server = ThreadingHTTPServer(("127.0.0.1", static_port), Handler)
    threading.Thread(target=static_server.serve_forever, daemon=True).start()

    base_url = f"http://127.0.0.1:{static_port}"
    sse_base = f"http://127.0.0.1:{sse_port}"

    # Test mode: set BATCH_MODE=True to simulate all events arriving at once
    BATCH_MODE = "--batch" in __import__("sys").argv

    if BATCH_MODE:
        print("\n== BATCH MODE: all events emitted at once (simulating fast job) ==")
        STEPS = [
            ("token_refresh", "Verificando credenciais ML...", 0),
            ("validate_category", "Validando atributos da categoria...", 0),
            ("downloading_images", "Baixando imagens do Drive...", 0),
            ("uploading_images", "Enviando imagens ao ML...", 0),
            ("creating_listing", "Criando anuncio no ML...", 0),
            ("checking_freight", "Consultando frete ML...", 0),
            ("activating", "Ativando anuncio...", 0),
            ("done", "Anuncio publicado!", 0),
        ]
    else:
        STEPS = [
            ("token_refresh", "Verificando credenciais ML...", 0.5),
            ("validate_category", "Validando atributos da categoria...", 0.3),
            ("downloading_images", "Baixando imagens do Drive...", 1.0),
            ("uploading_images", "Enviando imagens ao ML...", 0.8),
            ("creating_listing", "Criando anuncio no ML...", 0.6),
            ("checking_freight", "Consultando frete ML...", 0.4),
            ("activating", "Ativando anuncio...", 0.3),
            ("done", "Anuncio publicado!", 0),
        ]

    def emit_events_with_delays():
        time.sleep(0.5)  # Wait for SSE connection to establish
        for step, msg, delay in STEPS:
            extra = {}
            if step == "done":
                extra = {"listing_id": "MLB999TEST", "listing_url": "https://example.com/MLB999TEST"}
            sse_events.append({"step": step, "message": msg, **extra})
            print(f"  [server] emitted {step} at t={time.time():.3f}")
            if delay > 0:
                time.sleep(delay)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        def _json_response(route, payload, status=200):
            route.fulfill(status=status, headers={"Content-Type": "application/json"}, body=json.dumps(payload))

        products = json.loads((ROOT_DIR / "tests" / "fixtures" / "tiny" / "tiny_sku_fixture.json").read_text(encoding="utf-8")).get("products", {})
        products = {str(k).strip().upper(): v for k, v in products.items()}

        def handle_routes(route, request):
            url = urlparse(request.url)
            path = url.path
            method = request.method.upper()

            # Proxy SSE to real FastAPI server
            if "/api/ml/publish/" in path and path.endswith("/events"):
                route.continue_(url=f"{sse_base}{path}")
                return

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
                product = products.get(sku)
                if not product:
                    return _json_response(route, {"detail": "not found"}, status=404)
                ws = {
                    "id": f"ws-{sku.lower()}", "sku": sku, "sku_normalized": sku,
                    "marketplace": "mercadolivre", "marketplace_normalized": "mercadolivre",
                    "state_seq": 1, "updated_at": "2026-03-03T18:30:00",
                    "base_state": {"integration_mode": "tiny", "selected_marketplace": "mercadolivre",
                        "tiny_product_data": product,
                        "product_fields": {"product_name": product.get("title", ""), "tiny_sku_display": sku,
                            "tiny_cost_price": str(product.get("cost_price", 0)),
                            "tiny_shipping_cost": str(product.get("shipping_cost", 0)),
                            "tiny_height": str(product.get("height_cm", 0)),
                            "tiny_width": str(product.get("width_cm", 0)),
                            "tiny_length": str(product.get("length_cm", 0)),
                            "tiny_weight": str(product.get("weight_kg", 0)),
                            "tiny_gtin": str(product.get("gtin", "")),
                        },
                        "cost_price_cache": {}, "shipping_cost_cache": {},
                    },
                    "versioned_state": {"schema_version": 2, "variants": {
                        k: {"title": {"versions": [f"Title {sku}"], "current_index": 0},
                            "description": {"versions": [f"Desc {sku}"], "current_index": 0},
                            "faq_lines": [], "card_lines": []}
                        for k in ["simple", "kit2", "kit3", "kit4", "kit5"]
                    }},
                }
                return _json_response(route, {"source": "tiny", "workspace": ws})
            if path == "/api/sku/workspace/save" and method == "POST":
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
                return _json_response(route, {"shipping_cost": 5.0})
            if path == "/api/ml/publish" and method == "POST":
                threading.Thread(target=emit_events_with_delays, daemon=True).start()
                return _json_response(route, {"job_id": "test-job-123"})
            if path == "/api/canva/list":
                return _json_response(route, {"design": None})
            return route.continue_()

        context.route("**/*", handle_routes)

        page.goto(f"{base_url}/static/main.html", wait_until="domcontentloaded")
        page.wait_for_function("() => document.querySelector('#tinyInstance option[value=\"0\"]') !== null")
        page.select_option("#tinyInstance", "0")

        # Load SKU
        page.fill("#tinySKU", "NEWGD60C7")
        page.press("#tinySKU", "Enter")
        page.wait_for_function("(t) => document.querySelector('#tinySKUDisplay').value.toUpperCase().includes(t)", arg="NEWGD60C7")
        page.wait_for_function("() => (document.querySelector('#tinyCostPrice').value || '').trim().length > 0")
        page.wait_for_timeout(1500)

        # Fill shipping cost if empty (fixture may not have it)
        has_shipping = page.evaluate("() => parseFloat(document.getElementById('tinyShippingCost')?.value || '0') > 0")
        if not has_shipping:
            page.fill("#tinyShippingCost", "8.50")

        # Ensure title and description are filled (may be auto-generated)
        has_title = page.evaluate("() => !!document.getElementById('outTitle').value.trim()")
        if not has_title:
            page.fill("#outTitle", "Test Product Title SSE Debug")
        has_desc = page.evaluate("() => !!document.getElementById('outDesc').value.trim()")
        if not has_desc:
            page.fill("#outDesc", "Test product description for SSE debug")

        # Ensure ML category is selected
        page.evaluate("""() => {
            const sel = document.getElementById('mlCategorySelect');
            if (sel && !sel.value && sel.options.length > 1) {
                sel.value = sel.options[1].value;
            }
        }""")

        # Wait for pricing to load (announce price)
        page.wait_for_function("() => parseFloat(document.getElementById('tinyAnnouncePriceMin')?.value || '0') > 0", timeout=5000)

        # Monkey-patch EventSource to route to our SSE server
        page.evaluate(f"""() => {{
            const OrigEventSource = window.EventSource;
            window.EventSource = function(url) {{
                const newUrl = url.replace(window.location.origin, '{sse_base}');
                console.log('[SSE] Connecting to:', newUrl);
                return new OrigEventSource(newUrl);
            }};
        }}""")

        # Instrument mlRenderSteps to track timing
        page.evaluate("""() => {
            window.__sseStepLog = [];
            const origRender = window.mlRenderSteps;
            window.mlRenderSteps = function(currentStep, isFailed) {
                window.__sseStepLog.push({
                    step: currentStep,
                    time: Date.now(),
                    states: JSON.parse(JSON.stringify(mlPanelStepStates)),
                });
                return origRender.call(this, currentStep, isFailed);
            };
        }""")

        # Click publish button
        print("\n== Clicking Publish button ==")
        publish_btn = page.query_selector("#btnPublishMl")
        if not publish_btn:
            print("ERROR: Publish button not found")
            browser.close()
            return

        # Check if button is visible
        is_visible = page.evaluate("() => { const b = document.getElementById('btnPublishMl'); return b && b.offsetParent !== null; }")
        print(f"  Button visible: {is_visible}")

        # Check validation before clicking
        validation = page.evaluate("() => validateWorkspaceForMlPublish()")
        print(f"  Validation missing: {validation}")

        if validation:
            print("  ERROR: Validation would fail. Cannot test SSE.")
            browser.close()
            return

        publish_btn.click()

        # Wait for the panel to appear
        page.wait_for_function("() => document.getElementById('publishPanel')?.style.display !== 'none'", timeout=5000)
        print("  Panel opened!")

        # Wait for all events to process (events take ~4s server-side + queue delays)
        page.wait_for_timeout(12000)

        # Read the step log
        step_log = page.evaluate("() => window.__sseStepLog")
        print("\n== SSE Step Render Log ==")
        if step_log and len(step_log) > 0:
            t0 = step_log[0]["time"]
            for entry in step_log:
                dt = entry["time"] - t0
                done_steps = [k for k, v in entry["states"].items() if v == "done"]
                print(f"  t={dt:6.0f}ms  step={entry['step'] or 'null':25s}  done={done_steps}")

            intervals = []
            for i in range(1, len(step_log)):
                intervals.append(step_log[i]["time"] - step_log[i - 1]["time"])
            print(f"\n  Total renders: {len(step_log)}")
            print(f"  Intervals (ms): {intervals}")
            if intervals:
                print(f"  Min interval: {min(intervals):.0f}ms")
                print(f"  Max interval: {max(intervals):.0f}ms")
                print(f"  Avg interval: {sum(intervals) / len(intervals):.0f}ms")

            rapid_count = sum(1 for i in intervals if i < 50)
            print(f"\n  Events arriving < 50ms apart: {rapid_count} / {len(intervals)}")
            if rapid_count > len(intervals) * 0.5:
                print("  >> DIAGNOSIS: Events are batched! SSE streaming is not working properly.")
            else:
                print("  >> Events arrive progressively. SSE streaming works correctly!")
        else:
            print("  No step renders recorded!")
            panel_visible = page.evaluate("() => document.getElementById('publishPanel')?.style.display")
            print(f"  Panel display: {panel_visible}")

        browser.close()
        static_server.shutdown()


if __name__ == "__main__":
    main()
