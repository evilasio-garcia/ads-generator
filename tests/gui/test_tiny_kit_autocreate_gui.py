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
SKU = "NEWGD60C7"


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


def _load_fixture_product() -> dict:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    products = payload.get("products") or {}
    if SKU not in products:
        pytest.skip(f"Fixture sem SKU {SKU}: {FIXTURE_PATH}")
    return products[SKU]


def _make_workspace(product: dict, marketplace: str = "mercadolivre") -> dict:
    sku = str(product.get("sku") or "").strip().upper()
    title = str(product.get("title") or sku)
    gtin = str(product.get("gtin") or "")
    empty_variant = {
        "title": {"versions": [], "current_index": -1},
        "description": {"versions": [], "current_index": -1},
        "faq_lines": [],
        "card_lines": [],
    }
    return {
        "id": f"ws-{sku.lower()}-{marketplace}",
        "sku": sku,
        "sku_normalized": sku,
        "marketplace": marketplace,
        "marketplace_normalized": marketplace,
        "state_seq": 1,
        "updated_at": "2026-03-04T15:00:00",
        "base_state": {
            "integration_mode": "tiny",
            "selected_marketplace": marketplace,
            "tiny_product_data": {
                "sku": sku,
                "title": title,
                "gtin": gtin,
                "height_cm": float(product.get("height_cm") or 0),
                "width_cm": float(product.get("width_cm") or 0),
                "length_cm": float(product.get("length_cm") or 0),
                "weight_kg": float(product.get("weight_kg") or 0),
                "unit": str(product.get("unit") or "PCT"),
                "cost_price": float(product.get("cost_price") or 0),
                "shipping_cost": 0.0,
                "list_price": float(product.get("list_price") or 0),
                "promo_price": float(product.get("promo_price") or 0),
            },
            "product_fields": {
                "product_name": title,
                "tiny_gtin": gtin,
                "tiny_sku_display": sku,
                "tiny_unit": str(product.get("unit") or "PCT"),
                "tiny_height": str(product.get("height_cm") or ""),
                "tiny_width": str(product.get("width_cm") or ""),
                "tiny_length": str(product.get("length_cm") or ""),
                "tiny_weight": str(product.get("weight_kg") or ""),
                "tiny_cost_price": str(product.get("cost_price") or ""),
                "tiny_shipping_cost": "0.00",
            },
            "cost_price_cache": {},
            "shipping_cost_cache": {},
        },
        "versioned_state": {
            "schema_version": 2,
            "variants": {
                "simple": dict(empty_variant),
                "kit2": dict(empty_variant),
                "kit3": dict(empty_variant),
                "kit4": dict(empty_variant),
                "kit5": dict(empty_variant),
            },
            "prices": {},
        },
    }


def _quote_payload(cost: float, shipping: float) -> dict:
    base = float(cost) + float(shipping)
    listing = round(base * 1.5, 2)
    aggressive = round(base * 1.15, 2)
    promo = round(base * 1.25, 2)
    return {
        "listing_price": {
            "price": listing,
            "metrics": {"margin_percent": 20.0, "value_multiple": 1.5, "value_amount": round(listing - base, 2)},
        },
        "aggressive_price": {
            "price": aggressive,
            "metrics": {"margin_percent": 10.0, "value_multiple": 1.15, "value_amount": round(aggressive - base, 2)},
        },
        "promo_price": {
            "price": promo,
            "metrics": {"margin_percent": 15.0, "value_multiple": 1.25, "value_amount": round(promo - base, 2)},
        },
        "wholesale_tiers": [
            {
                "min_quantity": 3,
                "price": round(base * 1.10, 2),
                "metrics": {"margin_percent": 8.0, "value_multiple": 1.10, "value_amount": round(base * 0.10, 2)},
            },
            {
                "min_quantity": 6,
                "price": round(base * 1.08, 2),
                "metrics": {"margin_percent": 6.0, "value_multiple": 1.08, "value_amount": round(base * 0.08, 2)},
            },
            {
                "min_quantity": 9,
                "price": round(base * 1.06, 2),
                "metrics": {"margin_percent": 4.0, "value_multiple": 1.06, "value_amount": round(base * 0.06, 2)},
            },
        ],
    }


@pytest.mark.skipif(sync_playwright is None, reason="playwright is not installed")
def test_gui_kit_autodiscovery_autocreate_and_collision_flow():
    root_dir = Path(__file__).resolve().parents[2]
    product = _load_fixture_product()
    workspace = _make_workspace(product)

    resolve_calls = {"kit2": 0, "kit3": 0, "kit4": 0}
    create_calls = {"kit2": 0, "kit4": 0}
    created_kit2 = {"done": False}

    with _static_server(root_dir) as base_url:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"Chromium not available: {exc}")

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
                    return _json_response(route, {"source": "tiny", "workspace": workspace})

                if path == "/api/sku/workspace/save" and method == "POST":
                    return _json_response(route, {"ok": True, "saved": True, "workspace_id": workspace["id"], "history_id": "h-1", "reason": None})

                if path == "/api/canva/list" and method == "POST":
                    return _json_response(route, {"design": None})

                if path == "/pricing/quote" and method == "POST":
                    body = request.post_data_json or {}
                    return _json_response(route, _quote_payload(float(body.get("cost_price") or 0), float(body.get("shipping_cost") or 0)))

                if path == "/api/shipping/calculate_ml" and method == "POST":
                    return _json_response(route, {"shipping_cost": 0.0})

                if path == "/api/tiny/kit/resolve" and method == "POST":
                    body = request.post_data_json or {}
                    qty = int(body.get("kit_quantity") or 0)
                    if qty == 2:
                        resolve_calls["kit2"] += 1
                        if created_kit2["done"]:
                            return _json_response(
                                route,
                                {
                                    "status": "found",
                                    "resolved_sku": "NEWGD60C7CB2",
                                    "searched_candidates": ["NEWGD60C7CB2", "NEWGD60C7-CB2"],
                                    "from_cache": resolve_calls["kit2"] > 1,
                                    "create_available": False,
                                    "validation": {
                                        "is_kit_class": True,
                                        "only_base_sku": True,
                                        "quantity_matches": True,
                                        "total_component_qty": 2,
                                        "component_skus": ["NEWGD60C7"],
                                    },
                                    "message": "Kit valido encontrado",
                                },
                            )
                        return _json_response(
                            route,
                            {
                                "status": "missing",
                                "resolved_sku": None,
                                "searched_candidates": ["NEWGD60C7CB2", "NEWGD60C7-CB2"],
                                "from_cache": False,
                                "create_available": True,
                                "validation": None,
                                "message": "Nenhum kit valido",
                            },
                        )
                    if qty == 3:
                        resolve_calls["kit3"] += 1
                        return _json_response(
                            route,
                            {
                                "status": "found",
                                "resolved_sku": "NEWGD60C7-CB3",
                                "searched_candidates": ["NEWGD60C7CB3", "NEWGD60C7-CB3"],
                                "from_cache": False,
                                "create_available": False,
                                "validation": {
                                    "is_kit_class": True,
                                    "only_base_sku": True,
                                    "quantity_matches": True,
                                    "total_component_qty": 3,
                                    "component_skus": ["NEWGD60C7"],
                                },
                                "message": "Kit valido encontrado",
                            },
                        )
                    if qty == 4:
                        resolve_calls["kit4"] += 1
                        return _json_response(
                            route,
                            {
                                "status": "missing",
                                "resolved_sku": None,
                                "searched_candidates": ["NEWGD60C7CB4", "NEWGD60C7-CB4"],
                                "from_cache": False,
                                "create_available": True,
                                "validation": None,
                                "message": "Nenhum kit valido",
                            },
                        )
                    return _json_response(route, {"status": "missing", "resolved_sku": None, "create_available": False})

                if path == "/api/tiny/kit/suggest-name" and method == "POST":
                    body = request.post_data_json or {}
                    qty = int(body.get("kit_quantity") or 0)
                    unit_plural = str(body.get("unit_plural_override") or "PACOTES").strip().upper() or "PACOTES"
                    return _json_response(
                        route,
                        {
                            "status": "ok",
                            "combo_name": f"COMBO COM {qty} {unit_plural}: Produto Base Sugerido",
                            "unit_plural": unit_plural,
                        },
                    )

                if path == "/api/tiny/kit/create" and method == "POST":
                    body = request.post_data_json or {}
                    qty = int(body.get("kit_quantity") or 0)
                    assert isinstance(body.get("combo_name_override"), str)
                    assert ":" in str(body.get("combo_name_override") or "")
                    assert body.get("kit_volumes") == 1
                    assert float(body.get("promotional_price")) == 0.0
                    assert float(body.get("announcement_price")) > 0
                    assert float(body.get("kit_weight_kg")) > 0
                    assert float(body.get("kit_height_cm")) > 0
                    assert float(body.get("kit_width_cm")) > 0
                    assert float(body.get("kit_length_cm")) > 0
                    if "kit_description" in body:
                        assert isinstance(body.get("kit_description"), str)
                    assert body.get("base_unit_override")
                    if qty == 2:
                        create_calls["kit2"] += 1
                        created_kit2["done"] = True
                        return _json_response(
                            route,
                            {
                                "status": "created",
                                "resolved_sku": "NEWGD60C7CB2",
                                "tiny_product_id": "900",
                                "validation": {
                                    "is_kit_class": True,
                                    "only_base_sku": True,
                                    "quantity_matches": True,
                                    "total_component_qty": 2,
                                    "component_skus": ["NEWGD60C7"],
                                },
                                "message": "KIT cadastrado com sucesso no Tiny: NEWGD60C7CB2",
                            },
                        )
                    if qty == 4:
                        create_calls["kit4"] += 1
                        return _json_response(
                            route,
                            {
                                "detail": {
                                    "status": "error",
                                    "type": "kit_sku_collision",
                                    "message": "Nao foi possivel cadastrar o KIT automaticamente: o codigo NEWGD60C7CB4 ja existe no Tiny.",
                                }
                            },
                            status=409,
                        )
                    return _json_response(route, {"detail": {"message": "Unexpected qty"}}, status=500)

                return route.continue_()

            context.route("**/*", handle_routes)

            def switch_variant(variant_key: str):
                page.wait_for_function("() => (typeof variantSwitchInProgress === 'undefined') || !variantSwitchInProgress")
                page.click(f"button.variant-tab-btn[data-variant='{variant_key}']")
                page.wait_for_function(
                    "(target) => (typeof activeVariantKey !== 'undefined' ? activeVariantKey : '') === target",
                    arg=variant_key,
                )
                page.wait_for_function("() => (typeof variantSwitchInProgress === 'undefined') || !variantSwitchInProgress")

            page.goto(base_url + "/static/main.html", wait_until="domcontentloaded")
            page.wait_for_selector("#tinyInstance")
            page.select_option("#tinyInstance", "0")
            page.fill("#tinySKU", SKU)
            page.press("#tinySKU", "Enter")
            page.wait_for_function("() => document.querySelector('#productName').value.trim().length > 0")

            switch_variant("kit2")
            page.wait_for_function("() => (document.querySelector('#btnTinyCreateKit')?.style.display || '') !== 'none'")
            assert page.input_value("#tinySKUDisplay").strip() == "NEWGD60C7CB2"
            assert "Tiny SP" in (page.get_attribute("#btnTinyCreateKit", "title") or "")

            page.click("#btnTinyCreateKit")
            page.wait_for_function("() => document.querySelector('#promptModal')?.classList.contains('open') === true")
            page.wait_for_function("() => (document.querySelector('#promptText')?.value || '').includes(':')")
            page.click("#promptOk")
            page.wait_for_function("() => (document.querySelector('#tinySKUDisplay')?.value || '').trim() === 'NEWGD60C7CB2'")
            page.wait_for_function("() => (document.querySelector('#btnTinyCreateKit')?.style.display || '') === 'none'")

            switch_variant("simple")
            switch_variant("kit2")
            page.wait_for_function("() => (document.querySelector('#tinySKUDisplay')?.value || '').trim() === 'NEWGD60C7CB2'")
            assert resolve_calls["kit2"] == 1  # resolve inicial; depois usa estado local/cache sem reconsulta forçada
            assert create_calls["kit2"] == 1

            switch_variant("kit3")
            page.wait_for_function("() => (document.querySelector('#tinySKUDisplay')?.value || '').trim() === 'NEWGD60C7-CB3'")
            page.wait_for_function("() => (document.querySelector('#btnTinyCreateKit')?.style.display || '') === 'none'")

            switch_variant("kit4")
            page.wait_for_function("() => (document.querySelector('#btnTinyCreateKit')?.style.display || '') !== 'none'")
            page.click("#btnTinyCreateKit")
            page.wait_for_function("() => document.querySelector('#promptModal')?.classList.contains('open') === true")
            page.click("#promptOk")
            page.wait_for_function(
                """
                () => {
                    const container = document.getElementById('toastContainer');
                    if (!container) return false;
                    const toasts = Array.from(container.children || []);
                    return toasts.some((t) => (t.dataset.variant || '') === 'error' && (t.textContent || '').includes('NEWGD60C7CB4'));
                }
                """
            )
            assert create_calls["kit4"] == 1

            context.close()
            browser.close()


@pytest.mark.skipif(sync_playwright is None, reason="playwright is not installed")
def test_gui_kit_resolve_404_shows_create_button_and_found_hides_button():
    root_dir = Path(__file__).resolve().parents[2]
    product = _load_fixture_product()
    workspace = _make_workspace(product)

    with _static_server(root_dir) as base_url:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"Chromium not available: {exc}")

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
                    return _json_response(route, {"source": "tiny", "workspace": workspace})

                if path == "/api/sku/workspace/save" and method == "POST":
                    return _json_response(
                        route,
                        {"ok": True, "saved": True, "workspace_id": workspace["id"], "history_id": "h-1", "reason": None},
                    )

                if path == "/api/canva/list" and method == "POST":
                    return _json_response(route, {"design": None})

                if path == "/pricing/quote" and method == "POST":
                    body = request.post_data_json or {}
                    return _json_response(route, _quote_payload(float(body.get("cost_price") or 0), float(body.get("shipping_cost") or 0)))

                if path == "/api/shipping/calculate_ml" and method == "POST":
                    return _json_response(route, {"shipping_cost": 0.0})

                if path == "/api/tiny/kit/resolve" and method == "POST":
                    body = request.post_data_json or {}
                    qty = int(body.get("kit_quantity") or 0)
                    if qty == 2:
                        return _json_response(
                            route,
                            {
                                "detail": {
                                    "status": "error",
                                    "type": "not_found",
                                    "message": "Kit nao encontrado para o SKU base informado.",
                                }
                            },
                            status=404,
                        )
                    if qty == 3:
                        return _json_response(
                            route,
                            {
                                "status": "found",
                                "resolved_sku": "NEWGD60C7-CB3",
                                "searched_candidates": ["NEWGD60C7CB3", "NEWGD60C7-CB3"],
                                "from_cache": False,
                                "create_available": False,
                                "validation": {
                                    "is_kit_class": True,
                                    "only_base_sku": True,
                                    "quantity_matches": True,
                                    "total_component_qty": 3,
                                    "component_skus": ["NEWGD60C7"],
                                },
                                "message": "Kit valido encontrado",
                            },
                        )
                    return _json_response(route, {"status": "missing", "resolved_sku": None, "create_available": False})

                if path == "/api/tiny/kit/create" and method == "POST":
                    return _json_response(route, {"detail": {"message": "create should not be called in this scenario"}}, status=500)

                if path == "/api/tiny/kit/suggest-name" and method == "POST":
                    body = request.post_data_json or {}
                    qty = int(body.get("kit_quantity") or 0)
                    unit_plural = str(body.get("unit_plural_override") or "PACOTES").strip().upper() or "PACOTES"
                    return _json_response(
                        route,
                        {
                            "status": "ok",
                            "combo_name": f"COMBO COM {qty} {unit_plural}: Produto Base Sugerido",
                            "unit_plural": unit_plural,
                        },
                    )

                return route.continue_()

            context.route("**/*", handle_routes)

            def switch_variant(variant_key: str):
                page.wait_for_function("() => (typeof variantSwitchInProgress === 'undefined') || !variantSwitchInProgress")
                page.click(f"button.variant-tab-btn[data-variant='{variant_key}']")
                page.wait_for_function(
                    "(target) => (typeof activeVariantKey !== 'undefined' ? activeVariantKey : '') === target",
                    arg=variant_key,
                )
                page.wait_for_function("() => (typeof variantSwitchInProgress === 'undefined') || !variantSwitchInProgress")

            page.goto(base_url + "/static/main.html", wait_until="domcontentloaded")
            page.wait_for_selector("#tinyInstance")
            page.select_option("#tinyInstance", "0")
            page.fill("#tinySKU", SKU)
            page.press("#tinySKU", "Enter")
            page.wait_for_function("() => document.querySelector('#productName').value.trim().length > 0")

            # 404 not_found no resolve deve habilitar o botao de cadastro automatico
            switch_variant("kit2")
            page.wait_for_function("() => (document.querySelector('#btnTinyCreateKit')?.style.display || '') !== 'none'")
            page.wait_for_function("() => (document.querySelector('#tinySKUDisplay')?.value || '').trim() === 'NEWGD60C7CB2'")

            # Kit encontrado nao deve exibir botao
            switch_variant("kit3")
            page.wait_for_function("() => (document.querySelector('#tinySKUDisplay')?.value || '').trim() === 'NEWGD60C7-CB3'")
            page.wait_for_function("() => (document.querySelector('#btnTinyCreateKit')?.style.display || '') === 'none'")

            context.close()
            browser.close()


@pytest.mark.skipif(sync_playwright is None, reason="playwright is not installed")
def test_gui_kit_create_upstream_validation_error_shows_clear_toast_message():
    root_dir = Path(__file__).resolve().parents[2]
    product = _load_fixture_product()
    workspace = _make_workspace(product)

    with _static_server(root_dir) as base_url:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"Chromium not available: {exc}")

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
                    return _json_response(route, {"source": "tiny", "workspace": workspace})

                if path == "/api/sku/workspace/save" and method == "POST":
                    return _json_response(
                        route,
                        {"ok": True, "saved": True, "workspace_id": workspace["id"], "history_id": "h-1", "reason": None},
                    )

                if path == "/api/canva/list" and method == "POST":
                    return _json_response(route, {"design": None})

                if path == "/pricing/quote" and method == "POST":
                    body = request.post_data_json or {}
                    return _json_response(route, _quote_payload(float(body.get("cost_price") or 0), float(body.get("shipping_cost") or 0)))

                if path == "/api/shipping/calculate_ml" and method == "POST":
                    return _json_response(route, {"shipping_cost": 0.0})

                if path == "/api/tiny/kit/resolve" and method == "POST":
                    return _json_response(
                        route,
                        {
                            "status": "missing",
                            "resolved_sku": None,
                            "searched_candidates": ["NEWGD60C7CB2", "NEWGD60C7-CB2"],
                            "from_cache": False,
                            "create_available": True,
                            "validation": None,
                            "message": "Nenhum kit valido",
                        },
                    )

                if path == "/api/tiny/kit/create" and method == "POST":
                    body = request.post_data_json or {}
                    assert body.get("kit_quantity") == 2
                    assert isinstance(body.get("combo_name_override"), str)
                    assert ":" in str(body.get("combo_name_override") or "")
                    assert float(body.get("promotional_price")) == 0.0
                    assert float(body.get("announcement_price")) > 0
                    return _json_response(
                        route,
                        {
                            "detail": {
                                "status": "error",
                                "type": "upstream_error",
                                "message": "O preco do produto deve ser informado",
                            }
                        },
                        status=502,
                    )

                if path == "/api/tiny/kit/suggest-name" and method == "POST":
                    body = request.post_data_json or {}
                    qty = int(body.get("kit_quantity") or 0)
                    unit_plural = str(body.get("unit_plural_override") or "PACOTES").strip().upper() or "PACOTES"
                    return _json_response(
                        route,
                        {
                            "status": "ok",
                            "combo_name": f"COMBO COM {qty} {unit_plural}: Produto Base Sugerido",
                            "unit_plural": unit_plural,
                        },
                    )

                return route.continue_()

            context.route("**/*", handle_routes)

            page.goto(base_url + "/static/main.html", wait_until="domcontentloaded")
            page.wait_for_selector("#tinyInstance")
            page.select_option("#tinyInstance", "0")
            page.fill("#tinySKU", SKU)
            page.press("#tinySKU", "Enter")
            page.wait_for_function("() => document.querySelector('#productName').value.trim().length > 0")

            page.click("button.variant-tab-btn[data-variant='kit2']")
            page.wait_for_function("() => (document.querySelector('#btnTinyCreateKit')?.style.display || '') !== 'none'")

            page.click("#btnTinyCreateKit")
            page.wait_for_function("() => document.querySelector('#promptModal')?.classList.contains('open') === true")
            page.click("#promptOk")

            page.wait_for_function(
                """
                () => {
                    const container = document.getElementById('toastContainer');
                    if (!container) return false;
                    const toasts = Array.from(container.children || []);
                    return toasts.some((t) => (t.dataset.variant || '') === 'error' && (t.textContent || '').includes('O preco do produto deve ser informado'));
                }
                """
            )

            context.close()
            browser.close()
