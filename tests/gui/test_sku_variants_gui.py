import json
import math
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


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "tiny" / "tiny_sku_fixture.json"

HARD_CODED_PRICE_EXPECTATIONS = {
    "PTBOCSALMCATCX10": {
        "simple": {
            "cost": 22.26,
            "width": 11.00,
            "weight": 0.400,
            "shipping": 0.00,
            "announce_min": 32.26,
            "aggressive_min": 30.65,
            "promo_min": 29.03,
            "announce_max": 32.26,
            "aggressive_max": 30.65,
            "promo_max": 29.03,
            "wholesale": [{"qty": 2.0, "price": 29.68}, {"qty": 3.0, "price": 29.03}],
        },
        "kit2": {
            "cost": 44.52,
            "width": 22.00,
            "weight": 0.800,
            "shipping": 8.90,
            "announce_min": 63.42,
            "aggressive_min": 60.25,
            "promo_min": 57.08,
            "announce_max": 63.42,
            "aggressive_max": 60.25,
            "promo_max": 57.08,
            "wholesale": [{"qty": 2.0, "price": 58.35}, {"qty": 3.0, "price": 57.08}],
        },
        "kit3": {
            "cost": 66.78,
            "width": 33.00,
            "weight": 1.200,
            "shipping": 13.36,
            "announce_min": 90.14,
            "aggressive_min": 85.63,
            "promo_min": 81.13,
            "announce_max": 90.14,
            "aggressive_max": 85.63,
            "promo_max": 81.13,
            "wholesale": [{"qty": 2.0, "price": 82.93}, {"qty": 3.0, "price": 81.13}],
        },
        "kit4": {
            "cost": 89.04,
            "width": 44.00,
            "weight": 1.600,
            "shipping": 17.81,
            "announce_min": 116.85,
            "aggressive_min": 111.01,
            "promo_min": 105.16,
            "announce_max": 116.85,
            "aggressive_max": 111.01,
            "promo_max": 105.16,
            "wholesale": [{"qty": 2.0, "price": 107.50}, {"qty": 3.0, "price": 105.16}],
        },
        "kit5": {
            "cost": 111.30,
            "width": 55.00,
            "weight": 2.000,
            "shipping": 22.26,
            "announce_min": 143.56,
            "aggressive_min": 136.38,
            "promo_min": 129.20,
            "announce_max": 143.56,
            "aggressive_max": 136.38,
            "promo_max": 129.20,
            "wholesale": [{"qty": 2.0, "price": 132.08}, {"qty": 3.0, "price": 129.20}],
        },
    },
    "NEWGD60C7": {
        "simple": {
            "cost": 24.90,
            "width": 30.00,
            "weight": 0.200,
            "shipping": 0.00,
            "announce_min": 34.90,
            "aggressive_min": 33.15,
            "promo_min": 31.41,
            "announce_max": 34.90,
            "aggressive_max": 33.15,
            "promo_max": 31.41,
            "wholesale": [{"qty": 2.0, "price": 32.11}, {"qty": 3.0, "price": 31.41}],
        },
        "kit2": {
            "cost": 49.80,
            "width": 60.00,
            "weight": 0.400,
            "shipping": 9.96,
            "announce_min": 69.76,
            "aggressive_min": 66.27,
            "promo_min": 62.78,
            "announce_max": 69.76,
            "aggressive_max": 66.27,
            "promo_max": 62.78,
            "wholesale": [{"qty": 2.0, "price": 64.18}, {"qty": 3.0, "price": 62.78}],
        },
        "kit3": {
            "cost": 74.70,
            "width": 90.00,
            "weight": 0.600,
            "shipping": 14.94,
            "announce_min": 99.64,
            "aggressive_min": 94.66,
            "promo_min": 89.68,
            "announce_max": 99.64,
            "aggressive_max": 94.66,
            "promo_max": 89.68,
            "wholesale": [{"qty": 2.0, "price": 91.67}, {"qty": 3.0, "price": 89.68}],
        },
        "kit4": {
            "cost": 99.60,
            "width": 120.00,
            "weight": 0.800,
            "shipping": 19.92,
            "announce_min": 129.52,
            "aggressive_min": 123.04,
            "promo_min": 116.57,
            "announce_max": 129.52,
            "aggressive_max": 123.04,
            "promo_max": 116.57,
            "wholesale": [{"qty": 2.0, "price": 119.16}, {"qty": 3.0, "price": 116.57}],
        },
        "kit5": {
            "cost": 124.50,
            "width": 150.00,
            "weight": 1.000,
            "shipping": 24.90,
            "announce_min": 159.40,
            "aggressive_min": 151.43,
            "promo_min": 143.46,
            "announce_max": 159.40,
            "aggressive_max": 151.43,
            "promo_max": 143.46,
            "wholesale": [{"qty": 2.0, "price": 146.65}, {"qty": 3.0, "price": 143.46}],
        },
    },
}


def _metric_block(price: float) -> dict:
    return {
        "margin_percent": round(max(price, 0.0) * 0.01, 2),
        "value_multiple": round(max(price, 0.0) / 10.0, 2),
        "value_amount": round(max(price, 0.0) * 0.15, 2),
    }


def _load_tiny_fixture() -> dict:
    if not FIXTURE_PATH.exists():
        pytest.skip(f"Fixture Tiny nao encontrada: {FIXTURE_PATH}")
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    products = payload.get("products") or {}
    if not isinstance(products, dict) or len(products) < 2:
        pytest.skip("Fixture Tiny sem pelo menos 2 produtos validos.")
    return {str(k).strip().upper(): v for k, v in products.items()}


def _make_workspace_from_tiny(product: dict, marketplace: str = "mercadolivre") -> dict:
    sku = str(product.get("sku") or "").strip().upper()
    title = str(product.get("title") or f"Produto {sku}")
    gtin = str(product.get("gtin") or "")
    height = float(product.get("height_cm") or 0.0)
    width = float(product.get("width_cm") or 0.0)
    length = float(product.get("length_cm") or 0.0)
    weight = float(product.get("weight_kg") or 0.0)
    cost = float(product.get("cost_price") or 0.0)
    shipping = float(product.get("shipping_cost") or 0.0)

    return {
        "id": f"ws-{sku.lower()}-{marketplace}",
        "sku": sku,
        "sku_normalized": sku,
        "marketplace": marketplace,
        "marketplace_normalized": marketplace,
        "state_seq": 1,
        "updated_at": "2026-03-03T18:30:00",
        "base_state": {
            "integration_mode": "tiny",
            "selected_marketplace": marketplace,
            "tiny_product_data": {
                "sku": sku,
                "title": title,
                "gtin": gtin,
                "height_cm": height,
                "width_cm": width,
                "length_cm": length,
                "weight_kg": weight,
                "cost_price": cost,
                "shipping_cost": shipping,
                "list_price": float(product.get("list_price") or 0.0),
                "promo_price": float(product.get("promo_price") or 0.0),
            },
            "product_fields": {
                "product_name": title,
                "tiny_gtin": gtin,
                "tiny_sku_display": sku,
                "height_cm": f"{height:.2f}",
                "width_cm": f"{width:.2f}",
                "length_cm": f"{length:.2f}",
                "weight_kg": f"{weight:.3f}",
                "tiny_cost_price": f"{cost:.2f}",
                "tiny_shipping_cost": f"{shipping:.2f}",
            },
            "cost_price_cache": {},
            "shipping_cost_cache": {},
        },
        "versioned_state": {
            "schema_version": 2,
            "variants": {
                "simple": {
                    "title": {"versions": [f"Titulo {sku}"], "current_index": 0},
                    "description": {"versions": [f"Descricao {sku}"], "current_index": 0},
                    "faq_lines": [],
                    "card_lines": [],
                },
                "kit2": {"title": {"versions": [], "current_index": -1}, "description": {"versions": [], "current_index": -1}, "faq_lines": [], "card_lines": []},
                "kit3": {"title": {"versions": [], "current_index": -1}, "description": {"versions": [], "current_index": -1}, "faq_lines": [], "card_lines": []},
                "kit4": {"title": {"versions": [], "current_index": -1}, "description": {"versions": [], "current_index": -1}, "faq_lines": [], "card_lines": []},
                "kit5": {"title": {"versions": [], "current_index": -1}, "description": {"versions": [], "current_index": -1}, "faq_lines": [], "card_lines": []},
            },
            "prices": {},
        },
    }


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


def _json_response(route, payload: dict, status: int = 200):
    route.fulfill(
        status=status,
        headers={"Content-Type": "application/json"},
        body=json.dumps(payload, ensure_ascii=False),
    )


def _as_float(value) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _assert_close(actual: float, expected: float, label: str, tol: float = 0.02):
    assert math.isclose(actual, expected, rel_tol=0, abs_tol=tol), (
        f"{label}: esperado {expected}, recebido {actual}"
    )


@pytest.mark.skipif(sync_playwright is None, reason="playwright nao instalado no ambiente")
def test_gui_tiny_fake_fixture_sanity_and_variant_consistency():
    root_dir = Path(__file__).resolve().parents[2]
    products = _load_tiny_fixture()
    sku_a = "PTBOCSALMCATCX10"
    sku_b = "NEWGD60C7"
    if sku_a not in products or sku_b not in products:
        pytest.skip("Fixture Tiny nao contem os SKUs esperados para o teste.")

    fake_db = {}
    save_calls = []
    load_events = []

    with _static_server(root_dir) as base_url:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:  # pragma: no cover - env without browser binaries
                pytest.skip(f"Chromium indisponivel no ambiente: {exc}")

            context = browser.new_context()
            page = context.new_page()

            def handle_routes(route, request):
                path = urlparse(request.url).path
                method = request.method.upper()

                if path == "/api/config" and method == "GET":
                    return _json_response(
                        route,
                        {
                            "tiny_tokens": [{"label": "Tiny SP", "token": "token-sp"}],
                            "pricing_config": [
                                {
                                    "marketplace": "mercadolivre",
                                    "comissao_min": 12,
                                    "comissao_max": 17,
                                    "tacos": 5,
                                    "margem_contribuicao": 15,
                                    "lucro": 10,
                                    "impostos": 8,
                                }
                            ],
                        },
                    )

                if path == "/api/sku/workspace/load" and method == "POST":
                    body = request.post_data_json or {}
                    sku = str(body.get("sku") or "").strip().upper()
                    marketplace = str(body.get("marketplace") or "mercadolivre").strip().lower() or "mercadolivre"
                    key = (sku, marketplace)

                    if key in fake_db:
                        load_events.append({"sku": sku, "source": "db"})
                        return _json_response(route, {"source": "db", "workspace": fake_db[key]})

                    product = products.get(sku)
                    if not product:
                        return _json_response(
                            route,
                            {"detail": {"message": f"SKU {sku} nao encontrado no Tiny fake", "type": "not_found"}},
                            status=404,
                        )

                    ws = _make_workspace_from_tiny(product, marketplace=marketplace)
                    fake_db[key] = ws
                    load_events.append({"sku": sku, "source": "tiny"})
                    return _json_response(route, {"source": "tiny", "workspace": ws})

                if path == "/api/sku/workspace/save" and method == "POST":
                    body = request.post_data_json or {}
                    sku = str(body.get("sku") or "").strip().upper()
                    marketplace = str(body.get("marketplace") or "mercadolivre").strip().lower() or "mercadolivre"
                    key = (sku, marketplace)
                    ws = fake_db.get(key) or _make_workspace_from_tiny(products[sku], marketplace=marketplace)
                    ws["base_state"] = body.get("base_state") or {}
                    ws["versioned_state"] = body.get("versioned_state") or {}
                    ws["state_seq"] = int(ws.get("state_seq") or 0) + 1
                    fake_db[key] = ws
                    save_calls.append(
                        {
                            "sku": sku,
                            "marketplace": marketplace,
                            "payload": json.loads(json.dumps(body)),
                        }
                    )
                    return _json_response(
                        route,
                        {
                            "ok": True,
                            "saved": True,
                            "workspace_id": ws["id"],
                            "history_id": f"h-{ws['state_seq']}",
                            "reason": None,
                        },
                    )

                if path == "/pricing/quote" and method == "POST":
                    body = request.post_data_json or {}
                    cost = float(body.get("cost_price") or 0.0)
                    shipping = float(body.get("shipping_cost") or 0.0)
                    listing = round(cost + shipping + 10.0, 2)
                    aggressive = round(listing * 0.95, 2)
                    promo = round(listing * 0.9, 2)
                    return _json_response(
                        route,
                        {
                            "listing_price": {"price": listing, "metrics": _metric_block(listing)},
                            "aggressive_price": {"price": aggressive, "metrics": _metric_block(aggressive)},
                            "promo_price": {"price": promo, "metrics": _metric_block(promo)},
                            "wholesale_tiers": [
                                {"min_quantity": 2, "price": round(listing * 0.92, 2), "metrics": _metric_block(listing * 0.92)},
                                {"min_quantity": 3, "price": round(listing * 0.90, 2), "metrics": _metric_block(listing * 0.90)},
                            ],
                        },
                    )

                if path == "/api/canva/list" and method == "POST":
                    return _json_response(route, {"design": None})

                if path == "/api/shipping/calculate_ml" and method == "POST":
                    body = request.post_data_json or {}
                    decision_cost = float(body.get("cost_price") or 0.0)
                    return _json_response(route, {"shipping_cost": round(decision_cost * 0.1, 2)})

                return route.continue_()

            context.route("**/*", handle_routes)

            def ui_float(selector: str) -> float:
                return _as_float(page.eval_on_selector(selector, "el => el.value"))

            def wholesale_row_count() -> int:
                return int(page.evaluate("() => document.querySelectorAll('#wholesalePriceBody tr').length"))

            def assert_wholesale_rows_present(label: str):
                rows = wholesale_row_count()
                assert rows > 0, f"Tabela de atacado vazia em: {label}"

            def search_sku(sku: str):
                page.fill("#tinySKU", sku)
                page.wait_for_function("() => !document.querySelector('#btnTinySearch').disabled")
                page.click("#btnTinySearch")
                page.wait_for_function(
                    "(target) => document.querySelector('#tinySKUDisplay').value.toUpperCase().includes(target)",
                    arg=sku,
                )
                page.wait_for_function("() => (document.querySelector('#tinyCostPrice').value || '').trim().length > 0")

            def assert_simple_ui_matches_tiny(sku: str):
                product = products[sku]
                cost_ui = ui_float("#tinyCostPrice")
                cost_expected = float(product["cost_price"])
                if not math.isclose(cost_ui, cost_expected, rel_tol=0, abs_tol=0.02):
                    debug_state = page.evaluate(
                        "() => ({activeVariantKey, simpleCost: variantStore.simple.costPriceSnapshot, "
                        "activeCost: variantStore[activeVariantKey]?.costPriceSnapshot, "
                        "uiCost: document.querySelector('#tinyCostPrice')?.value || ''})"
                    )
                    raise AssertionError(
                        f"simple cost {sku}: esperado {cost_expected}, recebido {cost_ui}, debug={debug_state}"
                    )
                _assert_close(ui_float("#widthCm"), float(product["width_cm"]), f"simple width {sku}")
                _assert_close(ui_float("#weightKg"), float(product["weight_kg"]), f"simple weight {sku}", tol=0.005)
                gtin_ui = page.input_value("#tinyGTIN")
                assert str(product["gtin"]) in gtin_ui

            def assert_kit_ui_derived_from_simple(multiplier: int, sku: str):
                product = products[sku]
                _assert_close(ui_float("#tinyCostPrice"), float(product["cost_price"]) * multiplier, f"kit{multiplier} cost {sku}")
                _assert_close(ui_float("#widthCm"), float(product["width_cm"]) * multiplier, f"kit{multiplier} width {sku}")
                _assert_close(ui_float("#weightKg"), float(product["weight_kg"]) * multiplier, f"kit{multiplier} weight {sku}", tol=0.02)

            def switch_variant(variant_key: str):
                page.wait_for_function("() => (typeof variantSwitchInProgress === 'undefined') || !variantSwitchInProgress")
                page.click(f"button.variant-tab-btn[data-variant='{variant_key}']")
                page.wait_for_function(
                    "(target) => (typeof activeVariantKey !== 'undefined' ? activeVariantKey : '') === target",
                    arg=variant_key,
                )
                page.wait_for_function("() => (typeof variantSwitchInProgress === 'undefined') || !variantSwitchInProgress")
                page.wait_for_function("() => (document.querySelector('#tinyCostPrice').value || '').trim().length > 0")

            page.goto(f"{base_url}/static/main.html", wait_until="domcontentloaded")
            page.wait_for_function("() => document.querySelector('#tinyInstance option[value=\"0\"]') !== null")
            page.select_option("#tinyInstance", "0")

            # SKU A: first load from Tiny fake
            search_sku(sku_a)
            assert_simple_ui_matches_tiny(sku_a)
            page.wait_for_function("() => document.querySelectorAll('#wholesalePriceBody tr').length > 0")
            assert_wholesale_rows_present("simple first load")

            # switch through every kit tab and validate derivation
            for multiplier in (2, 3, 4, 5):
                switch_variant(f"kit{multiplier}")
                assert_kit_ui_derived_from_simple(multiplier, sku_a)
                assert_wholesale_rows_present(f"kit{multiplier}")
                simple_snapshot_cost_a = _as_float(page.evaluate("() => variantStore.simple.costPriceSnapshot || 0"))
                _assert_close(
                    simple_snapshot_cost_a,
                    float(products[sku_a]["cost_price"]),
                    f"simple snapshot cost A while on kit{multiplier}",
                )
                page.wait_for_timeout(850)  # debounce persist after tab switch
                last_save_a = next((c for c in reversed(save_calls) if c["sku"] == sku_a), None)
                assert last_save_a is not None
                fields_a = (((last_save_a["payload"] or {}).get("base_state") or {}).get("product_fields") or {})
                _assert_close(_as_float(fields_a.get("tiny_cost_price")), float(products[sku_a]["cost_price"]), f"db simple cost A on kit{multiplier}")
                _assert_close(_as_float(fields_a.get("width_cm")), float(products[sku_a]["width_cm"]), f"db simple width A on kit{multiplier}")
                _assert_close(_as_float(fields_a.get("weight_kg")), float(products[sku_a]["weight_kg"]), f"db simple weight A on kit{multiplier}", tol=0.005)

            # back to simple: no leakage from kit values
            switch_variant("simple")
            assert_simple_ui_matches_tiny(sku_a)
            assert_wholesale_rows_present("simple after kit loop")
            switch_variant("kit3")
            assert_wholesale_rows_present("kit3 revisit after returning from simple")
            switch_variant("simple")
            assert_wholesale_rows_present("simple after kit3 revisit")

            # SKU B: first load from Tiny fake
            search_sku(sku_b)
            assert_simple_ui_matches_tiny(sku_b)
            page.wait_for_function("() => document.querySelectorAll('#wholesalePriceBody tr').length > 0")
            assert_wholesale_rows_present("simple load sku B")

            for multiplier in (2, 3, 4, 5):
                switch_variant(f"kit{multiplier}")
                assert_kit_ui_derived_from_simple(multiplier, sku_b)
                simple_snapshot_cost_b = _as_float(page.evaluate("() => variantStore.simple.costPriceSnapshot || 0"))
                _assert_close(
                    simple_snapshot_cost_b,
                    float(products[sku_b]["cost_price"]),
                    f"simple snapshot cost B while on kit{multiplier}",
                )
                page.wait_for_timeout(850)
                last_save_b = next((c for c in reversed(save_calls) if c["sku"] == sku_b), None)
                assert last_save_b is not None
                fields_b = (((last_save_b["payload"] or {}).get("base_state") or {}).get("product_fields") or {})
                _assert_close(_as_float(fields_b.get("tiny_cost_price")), float(products[sku_b]["cost_price"]), f"db simple cost B on kit{multiplier}")
                _assert_close(_as_float(fields_b.get("width_cm")), float(products[sku_b]["width_cm"]), f"db simple width B on kit{multiplier}")
                _assert_close(_as_float(fields_b.get("weight_kg")), float(products[sku_b]["weight_kg"]), f"db simple weight B on kit{multiplier}", tol=0.005)

            # SKU A again: now should be DB hit and still equal to fixture/simple state
            search_sku(sku_a)
            assert_simple_ui_matches_tiny(sku_a)

            events_a = [e for e in load_events if e["sku"] == sku_a]
            events_b = [e for e in load_events if e["sku"] == sku_b]
            assert events_a and events_a[0]["source"] == "tiny"
            assert any(e["source"] == "db" for e in events_a[1:])
            assert events_b and events_b[0]["source"] == "tiny"

            ws_a = fake_db[(sku_a, "mercadolivre")]
            ws_fields_a = (((ws_a.get("base_state") or {}).get("product_fields")) or {})
            _assert_close(_as_float(ws_fields_a.get("tiny_cost_price")), ui_float("#tinyCostPrice"), "final db vs ui cost A")
            _assert_close(_as_float(ws_fields_a.get("width_cm")), ui_float("#widthCm"), "final db vs ui width A")
            _assert_close(_as_float(ws_fields_a.get("weight_kg")), ui_float("#weightKg"), "final db vs ui weight A", tol=0.005)
            _assert_close(_as_float(ws_fields_a.get("tiny_cost_price")), float(products[sku_a]["cost_price"]), "final db vs tiny cost A")
            _assert_close(_as_float(ws_fields_a.get("width_cm")), float(products[sku_a]["width_cm"]), "final db vs tiny width A")
            _assert_close(_as_float(ws_fields_a.get("weight_kg")), float(products[sku_a]["weight_kg"]), "final db vs tiny weight A", tol=0.005)

            browser.close()


@pytest.mark.skipif(sync_playwright is None, reason="playwright nao instalado no ambiente")
def test_gui_hardcoded_price_sanity_for_two_skus():
    root_dir = Path(__file__).resolve().parents[2]
    products = _load_tiny_fixture()
    expected_matrix = HARD_CODED_PRICE_EXPECTATIONS

    required_skus = list(expected_matrix.keys())
    for sku in required_skus:
        if sku not in products:
            pytest.skip(f"Fixture Tiny nao contem SKU esperado para sanity hardcoded: {sku}")

    fake_db = {}

    with _static_server(root_dir) as base_url:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:  # pragma: no cover - env without browser binaries
                pytest.skip(f"Chromium indisponivel no ambiente: {exc}")

            context = browser.new_context()
            page = context.new_page()

            def handle_routes(route, request):
                path = urlparse(request.url).path
                method = request.method.upper()

                if path == "/api/config" and method == "GET":
                    return _json_response(
                        route,
                        {
                            "tiny_tokens": [{"label": "Tiny SP", "token": "token-sp"}],
                            "pricing_config": [
                                {
                                    "marketplace": "mercadolivre",
                                    "comissao_min": 12,
                                    "comissao_max": 17,
                                    "tacos": 5,
                                    "margem_contribuicao": 15,
                                    "lucro": 10,
                                    "impostos": 8,
                                }
                            ],
                        },
                    )

                if path == "/api/sku/workspace/load" and method == "POST":
                    body = request.post_data_json or {}
                    sku = str(body.get("sku") or "").strip().upper()
                    marketplace = str(body.get("marketplace") or "mercadolivre").strip().lower() or "mercadolivre"
                    key = (sku, marketplace)

                    if key in fake_db:
                        return _json_response(route, {"source": "db", "workspace": fake_db[key]})

                    product = products.get(sku)
                    if not product:
                        return _json_response(
                            route,
                            {"detail": {"message": f"SKU {sku} nao encontrado no Tiny fake", "type": "not_found"}},
                            status=404,
                        )

                    ws = _make_workspace_from_tiny(product, marketplace=marketplace)
                    fake_db[key] = ws
                    return _json_response(route, {"source": "tiny", "workspace": ws})

                if path == "/api/sku/workspace/save" and method == "POST":
                    body = request.post_data_json or {}
                    sku = str(body.get("sku") or "").strip().upper()
                    marketplace = str(body.get("marketplace") or "mercadolivre").strip().lower() or "mercadolivre"
                    key = (sku, marketplace)
                    ws = fake_db.get(key) or _make_workspace_from_tiny(products[sku], marketplace=marketplace)
                    ws["base_state"] = body.get("base_state") or {}
                    ws["versioned_state"] = body.get("versioned_state") or {}
                    ws["state_seq"] = int(ws.get("state_seq") or 0) + 1
                    fake_db[key] = ws
                    return _json_response(
                        route,
                        {
                            "ok": True,
                            "saved": True,
                            "workspace_id": ws["id"],
                            "history_id": f"h-{ws['state_seq']}",
                            "reason": None,
                        },
                    )

                if path == "/pricing/quote" and method == "POST":
                    body = request.post_data_json or {}
                    cost = float(body.get("cost_price") or 0.0)
                    shipping = float(body.get("shipping_cost") or 0.0)
                    listing = round(cost + shipping + 10.0, 2)
                    aggressive = round(listing * 0.95, 2)
                    promo = round(listing * 0.9, 2)
                    return _json_response(
                        route,
                        {
                            "listing_price": {"price": listing, "metrics": _metric_block(listing)},
                            "aggressive_price": {"price": aggressive, "metrics": _metric_block(aggressive)},
                            "promo_price": {"price": promo, "metrics": _metric_block(promo)},
                            "wholesale_tiers": [
                                {"min_quantity": 2, "price": round(listing * 0.92, 2), "metrics": _metric_block(listing * 0.92)},
                                {"min_quantity": 3, "price": round(listing * 0.90, 2), "metrics": _metric_block(listing * 0.90)},
                            ],
                        },
                    )

                if path == "/api/canva/list" and method == "POST":
                    return _json_response(route, {"design": None})

                if path == "/api/shipping/calculate_ml" and method == "POST":
                    body = request.post_data_json or {}
                    decision_cost = float(body.get("cost_price") or 0.0)
                    return _json_response(route, {"shipping_cost": round(decision_cost * 0.1, 2)})

                return route.continue_()

            context.route("**/*", handle_routes)

            def ui_float(selector: str) -> float:
                return _as_float(page.eval_on_selector(selector, "el => el.value"))

            def read_wholesale_rows():
                return page.evaluate(
                    """() => Array.from(document.querySelectorAll('#wholesalePriceBody tr')).map((tr) => {
                        const price = parseFloat(tr.querySelector('input[data-field="price"]')?.value || 0);
                        const qty = parseFloat(tr.querySelector('input[data-field="qty"]')?.value || 0);
                        return { qty, price };
                    })"""
                )

            def search_sku(sku: str):
                page.fill("#tinySKU", sku)
                page.wait_for_function("() => !document.querySelector('#btnTinySearch').disabled")
                page.click("#btnTinySearch")
                page.wait_for_function(
                    "(target) => document.querySelector('#tinySKUDisplay').value.toUpperCase().includes(target)",
                    arg=sku,
                )
                page.wait_for_function("() => (document.querySelector('#tinyCostPrice').value || '').trim().length > 0")
                page.wait_for_function("() => (document.querySelector('#tinyPromoPriceMax').value || '').trim().length > 0")
                page.wait_for_function("() => document.querySelectorAll('#wholesalePriceBody tr').length >= 2")

            def switch_variant(variant_key: str):
                page.wait_for_function("() => (typeof variantSwitchInProgress === 'undefined') || !variantSwitchInProgress")
                page.click(f"button.variant-tab-btn[data-variant='{variant_key}']")
                page.wait_for_function(
                    "(target) => (typeof activeVariantKey !== 'undefined' ? activeVariantKey : '') === target",
                    arg=variant_key,
                )
                page.wait_for_function("() => (typeof variantSwitchInProgress === 'undefined') || !variantSwitchInProgress")
                page.wait_for_function("() => (document.querySelector('#tinyPromoPriceMax').value || '').trim().length > 0")
                page.wait_for_function("() => document.querySelectorAll('#wholesalePriceBody tr').length >= 2")

            def assert_variant_price_state(sku: str, variant_key: str):
                expected = expected_matrix[sku][variant_key]
                label = f"{sku}:{variant_key}"

                _assert_close(ui_float("#tinyCostPrice"), expected["cost"], f"{label} cost")
                _assert_close(ui_float("#widthCm"), expected["width"], f"{label} width")
                _assert_close(ui_float("#weightKg"), expected["weight"], f"{label} weight", tol=0.01)
                _assert_close(ui_float("#tinyShippingCost"), expected["shipping"], f"{label} shipping")

                _assert_close(ui_float("#tinyAnnouncePriceMin"), expected["announce_min"], f"{label} announce min")
                _assert_close(ui_float("#tinyAggressivePriceMin"), expected["aggressive_min"], f"{label} aggressive min")
                _assert_close(ui_float("#tinyPromoPriceMin"), expected["promo_min"], f"{label} promo min")
                _assert_close(ui_float("#tinyAnnouncePriceMax"), expected["announce_max"], f"{label} announce max")
                _assert_close(ui_float("#tinyAggressivePriceMax"), expected["aggressive_max"], f"{label} aggressive max")
                _assert_close(ui_float("#tinyPromoPriceMax"), expected["promo_max"], f"{label} promo max")

                rows = read_wholesale_rows()
                assert len(rows) == len(expected["wholesale"]), f"{label} atacado: quantidade de linhas inesperada"
                for idx, expected_row in enumerate(expected["wholesale"]):
                    _assert_close(_as_float(rows[idx]["qty"]), expected_row["qty"], f"{label} atacado qty row {idx}", tol=0.001)
                    _assert_close(_as_float(rows[idx]["price"]), expected_row["price"], f"{label} atacado price row {idx}")

            page.goto(f"{base_url}/static/main.html", wait_until="domcontentloaded")
            page.wait_for_function("() => document.querySelector('#tinyInstance option[value=\"0\"]') !== null")
            page.select_option("#tinyInstance", "0")

            for sku in required_skus:
                search_sku(sku)
                if page.evaluate("() => (typeof activeVariantKey !== 'undefined' ? activeVariantKey : '') !== 'simple'"):
                    switch_variant("simple")
                assert_variant_price_state(sku, "simple")
                for variant_key in ("kit2", "kit3", "kit4", "kit5"):
                    switch_variant(variant_key)
                    assert_variant_price_state(sku, variant_key)
                switch_variant("simple")
                assert_variant_price_state(sku, "simple")

            browser.close()
