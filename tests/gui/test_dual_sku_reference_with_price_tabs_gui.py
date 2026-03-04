import json
import socket
import threading
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - optional dependency
    sync_playwright = None


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "tiny" / "tiny_sku_fixture.json"
SKU_A = "NEWGD60C7"
SKU_B = "PTBOCSALMCATCX10"

TAB_SEQUENCE = [
    "simple",
    "kit2",
    "kit3",
    "kit4",
    "kit5",
    "kit4",
    "kit3",
    "kit2",
    "simple",
    "kit2",
    "kit3",
    "kit4",
    "kit5",
    "kit4",
    "kit3",
    "kit2",
    "simple",
]

NEW_REFERENCE = {
    "simple": {
        "base": {
            "gtin": "7898590070977",
            "sku": "NEWGD60C7",
            "height": "52",
            "width": "30",
            "length": "18",
            "weight": "0.2",
            "cost": "24.9",
            "shipping": "0.00",
        },
        "announce_min": {"price": "64.25", "margin": "28.24", "multiple": "1.37", "value": "18.14"},
        "aggressive_min": {"price": "48.94", "margin": "13.34", "multiple": "3.81", "value": "6.53"},
        "promo_min": {"price": "54.61", "margin": "19.54", "multiple": "2.33", "value": "10.67"},
        "wholesale": [
            {"price": "44.33", "qty": "9", "margin": "6.67", "multiple": "8.42", "value": "2.96"},
            {"price": "42.73", "qty": "15", "margin": "4.02", "multiple": "14.51", "value": "1.72"},
            {"price": "41.32", "qty": "40", "margin": "1.51", "multiple": "39.97", "value": "0.62"},
        ],
    },
    "kit2": {
        "base": {
            "gtin": "7898590070977",
            "sku": "NEWGD60C7",
            "height": "52",
            "width": "60.00",
            "length": "18",
            "weight": "0.400",
            "cost": "49.80",
            "shipping": "19.31",
        },
        "announce_min": {"price": "141.42", "margin": "28.63", "multiple": "1.23", "value": "40.49"},
        "aggressive_min": {"price": "107.71", "margin": "13.34", "multiple": "3.47", "value": "14.37"},
        "promo_min": {"price": "120.20", "margin": "20.00", "multiple": "2.07", "value": "24.05"},
        "wholesale": [
            {"price": "97.57", "qty": "8", "margin": "6.67", "multiple": "7.65", "value": "6.51"},
            {"price": "94.03", "qty": "14", "margin": "4.00", "multiple": "13.23", "value": "3.76"},
            {"price": "90.94", "qty": "37", "margin": "1.50", "multiple": "36.39", "value": "1.37"},
        ],
    },
    "kit3": {
        "base": {
            "gtin": "7898590070977",
            "sku": "NEWGD60C7",
            "height": "52",
            "width": "90.00",
            "length": "18",
            "weight": "0.600",
            "cost": "74.70",
            "shipping": "22.45",
        },
        "announce_min": {"price": "198.78", "margin": "28.63", "multiple": "1.31", "value": "56.90"},
        "aggressive_min": {"price": "151.41", "margin": "13.34", "multiple": "3.70", "value": "20.19"},
        "promo_min": {"price": "168.96", "margin": "20.00", "multiple": "2.21", "value": "33.79"},
        "wholesale": [
            {"price": "137.16", "qty": "9", "margin": "6.67", "multiple": "8.16", "value": "9.15"},
            {"price": "132.18", "qty": "15", "margin": "4.00", "multiple": "14.12", "value": "5.29"},
            {"price": "127.83", "qty": "39", "margin": "1.50", "multiple": "38.94", "value": "1.92"},
        ],
    },
    "kit4": {
        "base": {
            "gtin": "7898590070977",
            "sku": "NEWGD60C7",
            "height": "52",
            "width": "120.00",
            "length": "18",
            "weight": "0.800",
            "cost": "99.60",
            "shipping": "22.45",
        },
        "announce_min": {"price": "249.73", "margin": "28.63", "multiple": "1.39", "value": "71.49"},
        "aggressive_min": {"price": "190.21", "margin": "13.33", "multiple": "3.93", "value": "25.36"},
        "promo_min": {"price": "212.27", "margin": "20.00", "multiple": "2.35", "value": "42.46"},
        "wholesale": [
            {"price": "172.31", "qty": "9", "margin": "6.67", "multiple": "8.67", "value": "11.49"},
            {"price": "166.06", "qty": "15", "margin": "4.00", "multiple": "14.99", "value": "6.65"},
            {"price": "160.60", "qty": "42", "margin": "1.50", "multiple": "41.24", "value": "2.41"},
        ],
    },
    "kit5": {
        "base": {
            "gtin": "7898590070977",
            "sku": "NEWGD60C7",
            "height": "52",
            "width": "150.00",
            "length": "18",
            "weight": "1.000",
            "cost": "124.50",
            "shipping": "22.45",
        },
        "announce_min": {"price": "300.68", "margin": "28.63", "multiple": "1.45", "value": "86.08"},
        "aggressive_min": {"price": "229.02", "margin": "13.34", "multiple": "4.08", "value": "30.54"},
        "promo_min": {"price": "255.57", "margin": "20.00", "multiple": "2.44", "value": "51.12"},
        "wholesale": [
            {"price": "207.46", "qty": "9", "margin": "6.67", "multiple": "9.00", "value": "13.83"},
            {"price": "199.94", "qty": "16", "margin": "4.00", "multiple": "15.56", "value": "8.00"},
            {"price": "193.36", "qty": "43", "margin": "1.50", "multiple": "42.87", "value": "2.90"},
        ],
    },
}
PT_REFERENCE = {
    "simple": {
        "base": {
            "gtin": "17898959321839",
            "sku": "PTBOCSALMCATCX10",
            "height": "13",
            "width": "11",
            "length": "22",
            "weight": "0.4",
            "cost": "22.26",
            "shipping": "0.00",
        },
        "announce_min": {"price": "58.85", "margin": "28.21", "multiple": "1.34", "value": "16.60"},
        "aggressive_min": {"price": "44.83", "margin": "13.35", "multiple": "3.72", "value": "5.98"},
        "promo_min": {"price": "50.02", "margin": "19.50", "multiple": "2.28", "value": "9.76"},
        "wholesale": [
            {"price": "40.61", "qty": "9", "margin": "6.68", "multiple": "8.21", "value": "2.71"},
            {"price": "39.14", "qty": "15", "margin": "4.02", "multiple": "14.15", "value": "1.57"},
            {"price": "37.85", "qty": "39", "margin": "1.52", "multiple": "38.80", "value": "0.57"},
        ],
    },
    "kit2": {
        "base": {
            "gtin": "17898959321839",
            "sku": "PTBOCSALMCATCX10",
            "height": "13",
            "width": "22.00",
            "length": "22",
            "weight": "0.800",
            "cost": "44.52",
            "shipping": "20.21",
        },
        "announce_min": {"price": "146.28", "margin": "33.24", "multiple": "0.92", "value": "48.62"},
        "aggressive_min": {"price": "111.40", "margin": "19.39", "multiple": "2.06", "value": "21.61"},
        "promo_min": {"price": "124.32", "margin": "25.43", "multiple": "1.41", "value": "31.62"},
        "wholesale": [
            {"price": "100.92", "qty": "4", "margin": "13.36", "multiple": "3.30", "value": "13.48"},
            {"price": "97.26", "qty": "5", "margin": "10.95", "multiple": "4.18", "value": "10.65"},
            {"price": "94.06", "qty": "6", "margin": "8.68", "multiple": "5.45", "value": "8.17"},
        ],
    },
    "kit3": {
        "base": {
            "gtin": "17898959321839",
            "sku": "PTBOCSALMCATCX10",
            "height": "13",
            "width": "33.00",
            "length": "22",
            "weight": "1.200",
            "cost": "66.78",
            "shipping": "23.45",
        },
        "announce_min": {"price": "184.63", "margin": "28.63", "multiple": "1.26", "value": "52.86"},
        "aggressive_min": {"price": "140.62", "margin": "13.33", "multiple": "3.56", "value": "18.75"},
        "promo_min": {"price": "156.93", "margin": "20.00", "multiple": "2.13", "value": "31.39"},
        "wholesale": [
            {"price": "127.39", "qty": "8", "margin": "6.67", "multiple": "7.86", "value": "8.50"},
            {"price": "122.77", "qty": "14", "margin": "4.00", "multiple": "13.58", "value": "4.92"},
            {"price": "118.73", "qty": "38", "margin": "1.50", "multiple": "37.40", "value": "1.79"},
        ],
    },
    "kit4": {
        "base": {
            "gtin": "17898959321839",
            "sku": "PTBOCSALMCATCX10",
            "height": "13",
            "width": "44.00",
            "length": "22",
            "weight": "1.600",
            "cost": "89.04",
            "shipping": "23.45",
        },
        "announce_min": {"price": "230.17", "margin": "28.63", "multiple": "1.35", "value": "65.89"},
        "aggressive_min": {"price": "175.31", "margin": "13.33", "multiple": "3.81", "value": "23.38"},
        "promo_min": {"price": "195.64", "margin": "20.00", "multiple": "2.28", "value": "39.13"},
        "wholesale": [
            {"price": "158.81", "qty": "9", "margin": "6.67", "multiple": "8.41", "value": "10.59"},
            {"price": "153.06", "qty": "15", "margin": "4.01", "multiple": "14.52", "value": "6.13"},
            {"price": "148.03", "qty": "41", "margin": "1.51", "multiple": "39.87", "value": "2.23"},
        ],
    },
    "kit5": {
        "base": {
            "gtin": "17898959321839",
            "sku": "PTBOCSALMCATCX10",
            "height": "13",
            "width": "55.00",
            "length": "22",
            "weight": "2.000",
            "cost": "111.30",
            "shipping": "23.45",
        },
        "announce_min": {"price": "275.71", "margin": "28.63", "multiple": "1.40", "value": "78.93"},
        "aggressive_min": {"price": "210.01", "margin": "13.34", "multiple": "3.97", "value": "28.01"},
        "promo_min": {"price": "234.35", "margin": "20.00", "multiple": "2.37", "value": "46.87"},
        "wholesale": [
            {"price": "190.24", "qty": "9", "margin": "6.67", "multiple": "8.77", "value": "12.69"},
            {"price": "183.34", "qty": "16", "margin": "4.00", "multiple": "15.17", "value": "7.34"},
            {"price": "177.31", "qty": "42", "margin": "1.50", "multiple": "41.76", "value": "2.67"},
        ],
    },
}

REFERENCE_BY_SKU = {
    SKU_A: NEW_REFERENCE,
    SKU_B: PT_REFERENCE,
}

SHIPPING_BY_DECISION_COST = {
    "99.60": 19.31,
    "149.40": 22.45,
    "199.20": 22.45,
    "249.00": 22.45,
    "89.04": 20.21,
    "133.56": 23.45,
    "178.08": 23.45,
    "222.60": 23.45,
}


def _build_quote_reference_map():
    mapping = {}
    for sku_data in REFERENCE_BY_SKU.values():
        for variant_ref in sku_data.values():
            cost = Decimal(str(variant_ref["base"]["cost"])).quantize(Decimal("0.01"))
            shipping = Decimal(str(variant_ref["base"]["shipping"])).quantize(Decimal("0.01"))
            mapping[f"{cost:.2f}|{shipping:.2f}"] = variant_ref
    return mapping


QUOTE_REFERENCE_MAP = _build_quote_reference_map()


def _load_tiny_fixture() -> dict:
    if not FIXTURE_PATH.exists():
        pytest.skip(f"Tiny fixture not found: {FIXTURE_PATH}")
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    products = payload.get("products") or {}
    for sku in (SKU_A, SKU_B):
        if sku not in products:
            pytest.skip(f"Tiny fixture missing required SKU: {sku}")
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
                "tiny_height": f"{height:.2f}",
                "tiny_width": f"{width:.2f}",
                "tiny_length": f"{length:.2f}",
                "tiny_weight": f"{weight:.3f}",
                "tiny_cost_price": f"{cost:.2f}",
                "tiny_shipping_cost": f"{shipping:.2f}",
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


def _parse_decimal(value) -> Decimal:
    raw = str(value or "").strip()
    raw = raw.replace("R$", "").replace("%", "").replace("x", "").strip()
    raw = "".join(ch for ch in raw if ch.isdigit() or ch in ",.-")
    if not raw:
        return Decimal("0")
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise AssertionError(f"Invalid numeric value from UI: {value!r}") from exc


def _assert_exact_decimal(actual, expected, label: str):
    actual_dec = _parse_decimal(actual)
    expected_dec = Decimal(str(expected))
    assert actual_dec == expected_dec, f"{label}: expected {expected_dec}, got {actual_dec} (raw={actual!r})"


def _make_quote_response(payload):
    cost = Decimal(str(payload.get("cost_price") or 0)).quantize(Decimal("0.01"))
    shipping = Decimal(str(payload.get("shipping_cost") or 0)).quantize(Decimal("0.01"))
    key = f"{cost:.2f}|{shipping:.2f}"
    ref = QUOTE_REFERENCE_MAP.get(key)

    if not ref:
        raise AssertionError(f"Unexpected pricing combo in mock: cost={cost} shipping={shipping}")

    def block(price_ref):
        return {
            "price": float(price_ref["price"]),
            "metrics": {
                "margin_percent": float(price_ref["margin"]),
                "value_multiple": float(price_ref["multiple"]),
                "value_amount": float(price_ref["value"]),
            },
        }

    wholesale = []
    for row in ref["wholesale"]:
        wholesale.append(
            {
                "min_quantity": int(Decimal(row["qty"])),
                "price": float(row["price"]),
                "metrics": {
                    "margin_percent": float(row["margin"]),
                    "value_multiple": float(row["multiple"]),
                    "value_amount": float(row["value"]),
                },
            }
        )

    return {
        "listing_price": block(ref["announce_min"]),
        "aggressive_price": block(ref["aggressive_min"]),
        "promo_price": block(ref["promo_min"]),
        "wholesale_tiers": wholesale,
    }


def _shipping_for_decision_base(decision_cost: Decimal) -> float:
    decision_key = f"{decision_cost.quantize(Decimal('0.01')):.2f}"
    return SHIPPING_BY_DECISION_COST.get(decision_key, 0.0)


@pytest.mark.skipif(sync_playwright is None, reason="playwright is not installed")
def test_reference_dual_sku_navigation_with_price_tabs_and_refresh_stays_exact():
    root_dir = Path(__file__).resolve().parents[2]
    products = _load_tiny_fixture()
    fake_db = {}
    load_events = []

    with _static_server(root_dir) as base_url:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:  # pragma: no cover - env without browser binaries
                pytest.skip(f"Chromium not available in this environment: {exc}")

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
                            {"detail": {"message": f"SKU {sku} not found in Tiny fake", "type": "not_found"}},
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
                    payload = request.post_data_json or {}
                    return _json_response(route, _make_quote_response(payload))

                if path == "/api/shipping/calculate_ml" and method == "POST":
                    body = request.post_data_json or {}
                    decision_cost = Decimal(str(body.get("cost_price") or 0))
                    return _json_response(route, {"shipping_cost": _shipping_for_decision_base(decision_cost)})

                if path == "/api/canva/list" and method == "POST":
                    return _json_response(route, {"design": None})

                return route.continue_()

            context.route("**/*", handle_routes)

            def read_input(selector: str) -> str:
                return page.input_value(selector).strip()

            def read_price_block(field_id: str):
                script = """
                    (targetId) => {
                        const field = document.getElementById(targetId);
                        const parentContainer = field.closest('div').parentElement;
                        const grid = parentContainer.querySelector('.grid.grid-cols-3');
                        return {
                            price: field.value || '',
                            margin: grid.querySelector('.analysis-margin')?.value || '',
                            multiple: grid.querySelector('.analysis-multiple')?.value || '',
                            value: grid.querySelector('.analysis-value')?.value || '',
                        };
                    }
                """
                return page.evaluate(script, field_id)

            def read_wholesale_rows():
                script = """
                    () => Array.from(document.querySelectorAll('#wholesalePriceBody tr')).map((tr) => {
                        const price = tr.querySelector('input[data-field="price"]')?.value || '';
                        const qty = tr.querySelector('input[data-field="qty"]')?.value || '';
                        const cells = tr.querySelectorAll('td input[readonly]');
                        const margin = cells[0] ? cells[0].value : '';
                        const multiple = cells[1] ? cells[1].value : '';
                        const value = cells[2] ? cells[2].value : '';
                        return { price, qty, margin, multiple, value };
                    })
                """
                return page.evaluate(script)

            def switch_variant(variant_key: str):
                page.wait_for_function("() => (typeof variantSwitchInProgress === 'undefined') || !variantSwitchInProgress")
                active_variant = page.evaluate(
                    "() => (typeof activeVariantKey !== 'undefined' ? activeVariantKey : "
                    "(document.querySelector('.variant-tab-btn.active')?.dataset.variant || ''))"
                )
                if active_variant != variant_key:
                    page.click(f"button.variant-tab-btn[data-variant='{variant_key}']")
                page.wait_for_function(
                    "(target) => (typeof activeVariantKey !== 'undefined' ? activeVariantKey : '') === target",
                    arg=variant_key,
                )
                page.wait_for_function("() => (typeof variantSwitchInProgress === 'undefined') || !variantSwitchInProgress")
                page.wait_for_function("() => (document.querySelector('#tinyPromoPriceMin').value || '').trim().length > 0")
                page.wait_for_function("() => (document.querySelector('#tinyPromoPriceMax').value || '').trim().length > 0")
                page.wait_for_function("() => document.querySelectorAll('#wholesalePriceBody tr').length === 3")

            def assert_no_visible_versioning_controls_active(step_label: str):
                issues = page.evaluate(
                    """
                    () => {
                        const isVisible = (el) => {
                            if (!el) return false;
                            if (el.hidden) return false;
                            if (el.classList.contains('hidden')) return false;
                            const style = window.getComputedStyle(el);
                            if (!style || style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                            return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                        };

                        const issues = [];
                        const countSelectors = [
                            '#titleCount',
                            '#descCount',
                            '#aggressiveMinCount',
                            '#promoMinCount',
                            '#aggressiveMaxCount',
                            '#promoMaxCount',
                            '[data-role=\"count\"]',
                        ];
                        const countElements = Array.from(document.querySelectorAll(countSelectors.join(',')));
                        for (const el of countElements) {
                            if (!isVisible(el)) continue;
                            const text = (el.textContent || '').trim();
                            const m = text.match(/^(\\d+)\\s*\\/\\s*(\\d+)$/);
                            if (!m) continue;
                            const cur = Number(m[1]);
                            const total = Number(m[2]);
                            if (total > 1 || cur > 1) {
                                issues.push({
                                    kind: 'count',
                                    id: el.id || null,
                                    text,
                                });
                            }
                        }

                        const navSelectors = [
                            '#titlePrev',
                            '#titleNext',
                            '#descPrev',
                            '#descNext',
                            '#aggressiveMinPrev',
                            '#aggressiveMinNext',
                            '#promoMinPrev',
                            '#promoMinNext',
                            '#aggressiveMaxPrev',
                            '#aggressiveMaxNext',
                            '#promoMaxPrev',
                            '#promoMaxNext',
                            '#faqList [data-action=\"prev\"]',
                            '#faqList [data-action=\"next\"]',
                            '#cardsList [data-action=\"prev\"]',
                            '#cardsList [data-action=\"next\"]',
                        ];
                        const navButtons = Array.from(document.querySelectorAll(navSelectors.join(',')));
                        for (const btn of navButtons) {
                            if (!isVisible(btn)) continue;
                            issues.push({
                                kind: 'nav_button_visible',
                                id: btn.id || null,
                                action: btn.getAttribute('data-action') || null,
                            });
                        }

                        return issues;
                    }
                    """
                )
                assert not issues, (
                    f"{step_label}: versioning controls should not be active/visible, issues={issues}"
                )

            def toggle_max_min_tabs(step_label: str):
                page.click(".price-tab-btn[data-tab='max']")
                page.wait_for_function(
                    "() => document.querySelector('.price-tab-btn[data-tab=\"max\"]').classList.contains('active')"
                )
                page.wait_for_function(
                    "() => document.querySelector('.price-tab-content[data-tab=\"max\"]').classList.contains('active')"
                )
                page.wait_for_function("() => (document.querySelector('#tinyPromoPriceMax').value || '').trim().length > 0")

                page.click(".price-tab-btn[data-tab='min']")
                page.wait_for_function(
                    "() => document.querySelector('.price-tab-btn[data-tab=\"min\"]').classList.contains('active')"
                )
                page.wait_for_function(
                    "() => document.querySelector('.price-tab-content[data-tab=\"min\"]').classList.contains('active')"
                )
                page.wait_for_function("() => (document.querySelector('#tinyPromoPriceMin').value || '').trim().length > 0")

                assert page.evaluate(
                    "() => document.querySelector('.price-tab-btn[data-tab=\"min\"]').classList.contains('active')"
                ), f"{step_label}: min tab must be active after max/min toggle"
                assert_no_visible_versioning_controls_active(step_label)

            def assert_general_result_state(step_label: str):
                assert read_input("#outTitle") == "", f"{step_label}: title should stay empty"
                assert read_input("#outDesc") == "", f"{step_label}: description should stay empty"
                faq_count = int(page.evaluate("() => document.querySelectorAll('#faqList > *').length"))
                cards_count = int(page.evaluate("() => document.querySelectorAll('#cardsList > *').length"))
                assert faq_count == 0, f"{step_label}: FAQ must have 0 rows, got {faq_count}"
                assert cards_count == 0, f"{step_label}: Cards must have 0 rows, got {cards_count}"

            def assert_variant_matches_reference(sku: str, variant_key: str, step_label: str):
                ref = REFERENCE_BY_SKU[sku][variant_key]

                base = ref["base"]
                assert read_input("#tinyGTIN") == base["gtin"], (
                    f"{step_label} gtin: expected {base['gtin']}, got {read_input('#tinyGTIN')}"
                )
                assert read_input("#tinySKUDisplay").upper() == base["sku"], (
                    f"{step_label} sku: expected {base['sku']}, got {read_input('#tinySKUDisplay')}"
                )
                _assert_exact_decimal(read_input("#tinyHeight"), base["height"], f"{step_label} height")
                _assert_exact_decimal(read_input("#tinyWidth"), base["width"], f"{step_label} width")
                _assert_exact_decimal(read_input("#tinyLength"), base["length"], f"{step_label} length")
                _assert_exact_decimal(read_input("#tinyWeight"), base["weight"], f"{step_label} weight")
                _assert_exact_decimal(read_input("#tinyCostPrice"), base["cost"], f"{step_label} cost")
                _assert_exact_decimal(read_input("#tinyShippingCost"), base["shipping"], f"{step_label} shipping")

                announce = read_price_block("tinyAnnouncePriceMin")
                _assert_exact_decimal(announce["price"], ref["announce_min"]["price"], f"{step_label} announce/price")
                _assert_exact_decimal(announce["margin"], ref["announce_min"]["margin"], f"{step_label} announce/margin")
                _assert_exact_decimal(announce["multiple"], ref["announce_min"]["multiple"], f"{step_label} announce/multiple")
                _assert_exact_decimal(announce["value"], ref["announce_min"]["value"], f"{step_label} announce/value")

                aggressive = read_price_block("tinyAggressivePriceMin")
                _assert_exact_decimal(aggressive["price"], ref["aggressive_min"]["price"], f"{step_label} aggressive/price")
                _assert_exact_decimal(aggressive["margin"], ref["aggressive_min"]["margin"], f"{step_label} aggressive/margin")
                _assert_exact_decimal(aggressive["multiple"], ref["aggressive_min"]["multiple"], f"{step_label} aggressive/multiple")
                _assert_exact_decimal(aggressive["value"], ref["aggressive_min"]["value"], f"{step_label} aggressive/value")

                promo = read_price_block("tinyPromoPriceMin")
                _assert_exact_decimal(promo["price"], ref["promo_min"]["price"], f"{step_label} promo/price")
                _assert_exact_decimal(promo["margin"], ref["promo_min"]["margin"], f"{step_label} promo/margin")
                _assert_exact_decimal(promo["multiple"], ref["promo_min"]["multiple"], f"{step_label} promo/multiple")
                _assert_exact_decimal(promo["value"], ref["promo_min"]["value"], f"{step_label} promo/value")

                rows = read_wholesale_rows()
                assert len(rows) == len(ref["wholesale"]), (
                    f"{step_label} wholesale: expected {len(ref['wholesale'])} rows, got {len(rows)}"
                )
                for idx, expected_row in enumerate(ref["wholesale"]):
                    got = rows[idx]
                    _assert_exact_decimal(got["price"], expected_row["price"], f"{step_label} wholesale[{idx}] price")
                    _assert_exact_decimal(got["qty"], expected_row["qty"], f"{step_label} wholesale[{idx}] qty")
                    _assert_exact_decimal(got["margin"], expected_row["margin"], f"{step_label} wholesale[{idx}] margin")
                    _assert_exact_decimal(got["multiple"], expected_row["multiple"], f"{step_label} wholesale[{idx}] multiple")
                    _assert_exact_decimal(got["value"], expected_row["value"], f"{step_label} wholesale[{idx}] value")

                assert_general_result_state(step_label)

            def search_sku_with_enter(sku: str):
                page.fill("#tinySKU", sku)
                page.press("#tinySKU", "Enter")
                page.wait_for_function(
                    "(target) => document.querySelector('#tinySKUDisplay').value.toUpperCase().includes(target)",
                    arg=sku,
                )
                page.wait_for_function("() => (document.querySelector('#tinyPromoPriceMin').value || '').trim().length > 0")
                page.wait_for_function("() => (document.querySelector('#tinyPromoPriceMax').value || '').trim().length > 0")
                page.wait_for_function("() => document.querySelectorAll('#wholesalePriceBody tr').length === 3")

            def run_sku_flow(run_label: str, sku: str):
                search_sku_with_enter(sku)
                for step_idx, variant_key in enumerate(TAB_SEQUENCE, start=1):
                    switch_variant(variant_key)
                    step_label = f"{run_label}/{sku}/step-{step_idx}/{variant_key}"
                    toggle_max_min_tabs(step_label)
                    assert_variant_matches_reference(sku, variant_key, step_label)

            def run_full_cycle(run_label: str):
                run_sku_flow(run_label, SKU_A)
                run_sku_flow(run_label, SKU_B)

            page.goto(f"{base_url}/static/main.html", wait_until="domcontentloaded")
            page.wait_for_function("() => document.querySelector('#tinyInstance option[value=\"0\"]') !== null")
            page.select_option("#tinyInstance", "0")

            run_full_cycle("run1")

            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("() => document.querySelector('#tinyInstance option[value=\"0\"]') !== null")
            page.select_option("#tinyInstance", "0")

            run_full_cycle("run2")

            events_by_sku = {
                SKU_A: [event["source"] for event in load_events if event["sku"] == SKU_A],
                SKU_B: [event["source"] for event in load_events if event["sku"] == SKU_B],
            }
            for sku in (SKU_A, SKU_B):
                assert events_by_sku[sku], f"No load event registered for {sku}"
                assert events_by_sku[sku][0] == "tiny", (
                    f"First load for {sku} must come from tiny, got {events_by_sku[sku][0]!r}"
                )
                assert any(source == "db" for source in events_by_sku[sku][1:]), (
                    f"Expected at least one DB hit for {sku} after F5, got {events_by_sku[sku]}"
                )

            browser.close()
