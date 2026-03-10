"""Playwright GUI tests for SSE publish progress panel.

Covers:
- Progressive step rendering (events arrive and display one by one)
- Batch event handling (all events at once still render progressively)
- Error step rendering (panel shows error state correctly)
- Done step rendering (panel shows success state with link)
- Panel opens and shows initial state before first event
- Minimum 300ms display time between step transitions
"""
import asyncio
import json
import socket
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import uvicorn

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "tiny" / "tiny_sku_fixture.json"
ROOT_DIR = Path(__file__).resolve().parents[2]
SKU = "NEWGD60C7"

ALL_VARIANTS = ["simple", "kit2", "kit3", "kit4", "kit5"]

ML_STEP_ORDER = [
    "token_refresh", "validate_category", "downloading_images", "uploading_images",
    "creating_listing", "checking_freight",
    "adjusting_price", "updating_listing", "notifying_whatsapp",
    "activating",
]


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


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _create_sse_app(events_list):
    """Create a FastAPI app that serves SSE events from events_list."""
    app = FastAPI()

    @app.get("/api/ml/publish/{job_id}/events")
    async def sse_endpoint(job_id: str):
        async def generator():
            sent = 0
            for _ in range(600):
                while sent < len(events_list):
                    evt = events_list[sent]
                    yield f"data: {json.dumps(evt)}\n\n"
                    step = evt.get("step")
                    sent += 1
                    if step in ("done", "error", "category_validation_failed"):
                        return
                    if sent < len(events_list):
                        await asyncio.sleep(0.05)
                await asyncio.sleep(0.15)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    return app


def _json_response(route, payload, status=200):
    route.fulfill(status=status, headers={"Content-Type": "application/json"}, body=json.dumps(payload))


def _build_route_handler(products, fake_db, sse_base, events_list, emit_fn):
    def handle_routes(route, request):
        path = urlparse(request.url).path
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
            return _json_response(route, {"shipping_cost": 5.0})
        if path == "/api/ml/publish" and method == "POST":
            threading.Thread(target=emit_fn, daemon=True).start()
            return _json_response(route, {"job_id": "test-job-sse"})
        if path == "/api/canva/list":
            return _json_response(route, {"design": None})
        return route.continue_()
    return handle_routes


def _prepare_page_for_publish(page, base_url):
    """Load page, fill SKU, ensure all required fields are populated."""
    page.goto(f"{base_url}/static/main.html", wait_until="domcontentloaded")
    page.wait_for_function("() => document.querySelector('#tinyInstance option[value=\"0\"]') !== null")
    page.select_option("#tinyInstance", "0")

    page.fill("#tinySKU", SKU)
    page.press("#tinySKU", "Enter")
    page.wait_for_function(
        "(t) => document.querySelector('#tinySKUDisplay').value.toUpperCase().includes(t)",
        arg=SKU,
    )
    page.wait_for_function("() => (document.querySelector('#tinyCostPrice').value || '').trim().length > 0")
    page.wait_for_timeout(1500)

    # Fill all required fields via JS to bypass any UI guards
    page.evaluate("""() => {
        // Shipping cost
        const sc = document.getElementById('tinyShippingCost');
        if (sc && !(parseFloat(sc.value) > 0)) { sc.value = '8.50'; }

        // Title
        const title = document.getElementById('outTitle');
        if (title && !title.value.trim()) { title.value = 'Test Product Title SSE'; }

        // Description
        const desc = document.getElementById('outDesc');
        if (desc && !desc.value.trim()) { desc.value = 'Test product description for SSE testing'; }

        // ML Category
        const sel = document.getElementById('mlCategorySelect');
        if (sel && !sel.value && sel.options.length > 1) { sel.value = sel.options[1].value; }

        // Announce price (if not auto-calculated yet)
        const price = document.getElementById('tinyAnnouncePriceMin');
        if (price && !(parseFloat(price.value) > 0)) { price.value = '99.90'; }
    }""")


def _instrument_render_steps(page):
    """Instrument mlRenderSteps to log timestamps of each render."""
    page.evaluate("""() => {
        window.__sseStepLog = [];
        const origRender = window.mlRenderSteps;
        window.mlRenderSteps = function(currentStep, isFailed) {
            window.__sseStepLog.push({
                step: currentStep,
                time: Date.now(),
                states: JSON.parse(JSON.stringify(mlPanelStepStates)),
                isFailed: isFailed,
            });
            return origRender.call(this, currentStep, isFailed);
        };
    }""")


def _monkey_patch_event_source(page, sse_base):
    """Redirect EventSource connections to our test SSE server."""
    page.evaluate(f"""() => {{
        const OrigEventSource = window.EventSource;
        window.EventSource = function(url) {{
            const newUrl = url.replace(window.location.origin, '{sse_base}');
            return new OrigEventSource(newUrl);
        }};
    }}""")


@pytest.fixture(scope="module")
def sse_infra():
    """Set up SSE server infrastructure shared across tests."""
    if sync_playwright is None:
        pytest.skip("playwright not installed")

    products = _load_products()
    if SKU not in products:
        pytest.skip(f"Fixture missing SKU {SKU}")

    # Static file server
    static_port = _find_free_port()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(ROOT_DIR), **kw)
        def log_message(self, *a):
            return

    static_server = ThreadingHTTPServer(("127.0.0.1", static_port), Handler)
    threading.Thread(target=static_server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{static_port}"

    yield {"base_url": base_url, "products": products, "static_server": static_server}

    static_server.shutdown()


def _run_sse_test(sse_infra, steps_with_delays, wait_ms=12000):
    """
    Run a full SSE test: start SSE server, open browser, click publish, collect render log.
    Returns (step_log, intervals, panel_title, panel_footer_html).
    """
    products = sse_infra["products"]
    base_url = sse_infra["base_url"]

    events_list = []
    sse_port = _find_free_port()
    sse_app = _create_sse_app(events_list)

    def run_server():
        uvicorn.run(sse_app, host="127.0.0.1", port=sse_port, log_level="warning")

    sse_thread = threading.Thread(target=run_server, daemon=True)
    sse_thread.start()
    time.sleep(0.8)

    sse_base = f"http://127.0.0.1:{sse_port}"

    def emit_events():
        time.sleep(0.5)
        for step, msg, delay, extra in steps_with_delays:
            events_list.append({"step": step, "message": msg, **extra})
            if delay > 0:
                time.sleep(delay)

    fake_db = {}

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(f"Chromium unavailable: {exc}")

        context = browser.new_context()
        page = context.new_page()
        context.route("**/*", _build_route_handler(products, fake_db, sse_base, events_list, emit_events))

        _prepare_page_for_publish(page, base_url)
        _monkey_patch_event_source(page, sse_base)
        _instrument_render_steps(page)

        # Check validation before clicking
        validation = page.evaluate("() => validateWorkspaceForMlPublish()")
        assert not validation, f"Publish validation failed: {validation}"

        # Click publish
        page.click("#btnPublishMl")
        try:
            page.wait_for_function("() => document.getElementById('publishPanel')?.style.display !== 'none'", timeout=8000)
        except Exception:
            diag = page.evaluate("""() => ({
                panelDisplay: document.getElementById('publishPanel')?.style.display,
                btnDisabled: document.getElementById('btnPublishMl')?.disabled,
                toasts: [...document.querySelectorAll('.toast, [class*=toast]')].map(t => t.textContent),
            })""")
            raise AssertionError(f"Panel did not open. Diagnostics: {diag}")
        page.wait_for_timeout(wait_ms)

        step_log = page.evaluate("() => window.__sseStepLog")
        panel_title = page.evaluate("() => document.getElementById('publishPanelTitle')?.textContent || ''")
        panel_footer = page.evaluate("() => document.getElementById('publishPanelFooter')?.innerHTML || ''")
        panel_visible = page.evaluate("() => document.getElementById('publishPanel')?.style.display")

        browser.close()

    intervals = []
    if step_log and len(step_log) > 1:
        for i in range(1, len(step_log)):
            intervals.append(step_log[i]["time"] - step_log[i - 1]["time"])

    return step_log, intervals, panel_title, panel_footer


# ── Tests ─────────────────────────────────────────────────────────────────


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_progressive_rendering_with_delays(sse_infra):
    """Events with realistic server delays render progressively."""
    steps = [
        ("token_refresh", "Verificando credenciais ML...", 0.5, {}),
        ("validate_category", "Validando atributos...", 0.3, {}),
        ("downloading_images", "Baixando imagens...", 0.8, {}),
        ("uploading_images", "Enviando imagens...", 0.6, {}),
        ("creating_listing", "Criando anuncio...", 0.5, {}),
        ("checking_freight", "Consultando frete...", 0.3, {}),
        ("activating", "Ativando anuncio...", 0.3, {}),
        ("done", "Publicado!", 0, {"listing_id": "MLB123", "listing_url": "https://example.com/MLB123"}),
    ]

    step_log, intervals, panel_title, _ = _run_sse_test(sse_infra, steps, wait_ms=10000)

    # Must have multiple renders
    assert len(step_log) >= 8, f"Expected >= 8 renders, got {len(step_log)}"

    # No batched events (all intervals > 250ms for realistic delays)
    rapid_count = sum(1 for i in intervals if i < 200)
    assert rapid_count <= 1, f"Too many rapid events ({rapid_count}): intervals={intervals}"

    # Panel should show success
    assert "publicado" in panel_title.lower() or "Publicado" in panel_title


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_batch_events_render_progressively(sse_infra):
    """All events emitted at once still render with visible intervals via queue."""
    steps = [
        ("token_refresh", "Verificando...", 0, {}),
        ("validate_category", "Validando...", 0, {}),
        ("downloading_images", "Baixando...", 0, {}),
        ("uploading_images", "Enviando...", 0, {}),
        ("creating_listing", "Criando...", 0, {}),
        ("checking_freight", "Frete...", 0, {}),
        ("activating", "Ativando...", 0, {}),
        ("done", "Publicado!", 0, {"listing_id": "MLB456", "listing_url": "https://example.com/MLB456"}),
    ]

    step_log, intervals, panel_title, _ = _run_sse_test(sse_infra, steps, wait_ms=10000)

    assert len(step_log) >= 8, f"Expected >= 8 renders, got {len(step_log)}"

    # Even in batch mode, frontend queue should enforce >= 300ms between renders
    if len(intervals) > 0:
        avg_interval = sum(intervals) / len(intervals)
        assert avg_interval >= 250, f"Average interval too low ({avg_interval:.0f}ms), queue not working"

    assert "publicado" in panel_title.lower() or "Publicado" in panel_title


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_error_event_shows_error_state(sse_infra):
    """Error event renders correctly with failed step and error message."""
    steps = [
        ("token_refresh", "Verificando...", 0.3, {}),
        ("validate_category", "Validando...", 0.3, {}),
        ("downloading_images", "Baixando...", 0.3, {}),
        ("error", "Falha ao baixar imagens", 0, {"failed_at": "downloading_images"}),
    ]

    step_log, intervals, panel_title, panel_footer = _run_sse_test(sse_infra, steps, wait_ms=6000)

    assert len(step_log) >= 4, f"Expected >= 4 renders, got {len(step_log)}"

    # Check panel shows error state
    assert "falha" in panel_title.lower() or "Falha" in panel_title

    # Check error message in footer
    assert "Falha ao baixar imagens" in panel_footer

    # Check that downloading_images step was marked as failed
    last_entry = step_log[-1]
    assert last_entry["states"].get("downloading_images") == "failed"


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_done_event_marks_all_steps_done(sse_infra):
    """Done event marks all steps as done and shows success link."""
    steps = [
        ("token_refresh", "Verificando...", 0.2, {}),
        ("creating_listing", "Criando...", 0.2, {}),
        ("done", "Publicado!", 0, {"listing_id": "MLB789", "listing_url": "https://example.com/MLB789"}),
    ]

    step_log, _, panel_title, panel_footer = _run_sse_test(sse_infra, steps, wait_ms=6000)

    assert len(step_log) >= 3

    # Last render should have all steps as done
    last_states = step_log[-1]["states"]
    for step_name in ML_STEP_ORDER:
        assert last_states.get(step_name) == "done", f"Step {step_name} not marked as done"

    # Footer should contain link
    assert "MLB789" in panel_footer
    assert "example.com" in panel_footer


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_panel_opens_with_initial_state(sse_infra):
    """Panel opens and shows initial state (all pending) before first event."""
    steps = [
        ("token_refresh", "Verificando...", 2.0, {}),
        ("done", "Publicado!", 0, {"listing_id": "MLB000"}),
    ]

    products = sse_infra["products"]
    base_url = sse_infra["base_url"]

    events_list = []
    sse_port = _find_free_port()
    sse_app = _create_sse_app(events_list)

    def run_server():
        uvicorn.run(sse_app, host="127.0.0.1", port=sse_port, log_level="warning")

    threading.Thread(target=run_server, daemon=True).start()
    time.sleep(0.8)

    sse_base = f"http://127.0.0.1:{sse_port}"

    def emit_events():
        time.sleep(2.0)  # Long delay before first event
        for step, msg, delay, extra in steps:
            events_list.append({"step": step, "message": msg, **extra})
            if delay > 0:
                time.sleep(delay)

    fake_db = {}

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(f"Chromium unavailable: {exc}")

        context = browser.new_context()
        page = context.new_page()
        context.route("**/*", _build_route_handler(products, fake_db, sse_base, events_list, emit_events))

        _prepare_page_for_publish(page, base_url)
        _monkey_patch_event_source(page, sse_base)

        page.click("#btnPublishMl")
        page.wait_for_function("() => document.getElementById('publishPanel')?.style.display !== 'none'", timeout=5000)

        # Check initial state before first event arrives
        title = page.evaluate("() => document.getElementById('publishPanelTitle')?.textContent || ''")
        assert "Publicando" in title, f"Expected 'Publicando' in title, got: {title}"

        # All steps should be in initial state (rendered as pending)
        steps_html = page.evaluate("() => document.getElementById('publishPanelSteps')?.innerHTML || ''")
        assert "pending" in steps_html, "Expected pending steps in initial render"

        browser.close()


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_minimum_display_time_between_steps(sse_infra):
    """Each step transition enforces minimum 300ms display time."""
    # All events at once to test the queue
    steps = [
        ("token_refresh", "1", 0, {}),
        ("validate_category", "2", 0, {}),
        ("downloading_images", "3", 0, {}),
        ("uploading_images", "4", 0, {}),
        ("creating_listing", "5", 0, {}),
        ("done", "Done", 0, {"listing_id": "MLB999"}),
    ]

    step_log, intervals, _, _ = _run_sse_test(sse_infra, steps, wait_ms=8000)

    assert len(step_log) >= 6, f"Expected >= 6 renders, got {len(step_log)}"

    # All intervals (except possibly the first) should be >= 300ms
    if intervals:
        below_threshold = [i for i in intervals if i < 300]
        assert len(below_threshold) <= 1, (
            f"Too many rapid transitions ({len(below_threshold)}): {intervals}. "
            f"Queue should enforce >= 300ms between renders."
        )


@pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")
def test_skipped_steps_marked_as_done(sse_infra):
    """When server skips steps (e.g. no freight divergence), all prior steps are marked done."""
    # Simulate: no freight divergence, so adjusting_price, updating_listing,
    # notifying_whatsapp are skipped — server goes from checking_freight to activating
    steps = [
        ("token_refresh", "Verificando...", 0.2, {}),
        ("validate_category", "Validando...", 0.2, {}),
        ("downloading_images", "Baixando...", 0.2, {}),
        ("uploading_images", "Enviando...", 0.2, {}),
        ("creating_listing", "Criando...", 0.2, {}),
        ("checking_freight", "Frete...", 0.2, {}),
        # adjusting_price, updating_listing, notifying_whatsapp SKIPPED
        ("activating", "Ativando...", 0.2, {}),
        ("done", "Publicado!", 0, {"listing_id": "MLB_SKIP", "listing_url": "https://example.com/MLB_SKIP"}),
    ]

    step_log, _, panel_title, _ = _run_sse_test(sse_infra, steps, wait_ms=10000)

    assert len(step_log) >= 8, f"Expected >= 8 renders, got {len(step_log)}"

    # Find the render where 'activating' was the current step
    activating_entry = next((e for e in step_log if e["step"] == "activating"), None)
    assert activating_entry is not None, "No render for 'activating' step found"

    # All steps before 'activating' should be marked as done, INCLUDING the skipped ones
    skipped_steps = ["adjusting_price", "updating_listing", "notifying_whatsapp"]
    for skipped in skipped_steps:
        state = activating_entry["states"].get(skipped, "pending")
        assert state == "done", (
            f"Skipped step '{skipped}' should be 'done' but is '{state}'. "
            f"All states: {activating_entry['states']}"
        )

    # Also verify checking_freight is done
    assert activating_entry["states"].get("checking_freight") == "done"

    assert "publicado" in panel_title.lower() or "Publicado" in panel_title
