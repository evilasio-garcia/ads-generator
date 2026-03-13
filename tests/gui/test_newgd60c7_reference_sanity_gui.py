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
SKU = "NEWGD60C7"

# Valores de referência extraídos dos prints:
# NEWGD60C7-AnúncioSimples(dados válidos).jpeg
# NEWGD60C7-KitCom2(dados válidos).jpeg
# NEWGD60C7-KitCom3(dados válidos).jpeg
# NEWGD60C7-KitCom4(dados válidos).jpeg
# NEWGD60C7-KitCom5(dados válidos).jpeg
REFERENCE_BY_VARIANT = {
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
            "gtin": "",
            "sku": "NEWGD60C7CB2",
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
            "gtin": "",
            "sku": "NEWGD60C7CB3",
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
            "gtin": "",
            "sku": "NEWGD60C7CB4",
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
            "gtin": "",
            "sku": "NEWGD60C7CB5",
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


def _load_tiny_fixture() -> dict:
    if not FIXTURE_PATH.exists():
        pytest.skip(f"Fixture Tiny nao encontrada: {FIXTURE_PATH}")
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    products = payload.get("products") or {}
    if SKU not in products:
        pytest.skip(f"Fixture Tiny sem SKU esperado: {SKU}")
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


def _parse_decimal(value) -> Decimal:
    raw = str(value or "").strip()
    raw = raw.replace("R$", "").replace("%", "").replace("x", "").replace("∞", "").strip()
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
        raise AssertionError(f"Valor numerico invalido na UI: {value!r}") from exc


def _assert_exact_decimal(actual, expected, label: str):
    actual_dec = _parse_decimal(actual)
    expected_dec = Decimal(str(expected))
    assert actual_dec == expected_dec, f"{label}: esperado {expected_dec}, recebido {actual_dec} (raw={actual!r})"


def _make_quote_response(payload):
    # Resposta de pricing determinística para reproduzir EXATAMENTE os valores dos prints.
    cost = Decimal(str(payload.get("cost_price") or 0))
    shipping = Decimal(str(payload.get("shipping_cost") or 0))
    key = f"{cost:.2f}|{shipping:.2f}"

    mapping = {
        "24.90|0.00": REFERENCE_BY_VARIANT["simple"],
        "49.80|19.31": REFERENCE_BY_VARIANT["kit2"],
        "74.70|22.45": REFERENCE_BY_VARIANT["kit3"],
        "99.60|22.45": REFERENCE_BY_VARIANT["kit4"],
        "124.50|22.45": REFERENCE_BY_VARIANT["kit5"],
    }
    ref = mapping.get(key)
    if not ref:
        # Fallback controlado para permitir que o teste falhe no assert de tela
        # (com passo/aba), em vez de abortar no mock HTTP.
        by_cost = {
            Decimal("24.90"): REFERENCE_BY_VARIANT["simple"],
            Decimal("49.80"): REFERENCE_BY_VARIANT["kit2"],
            Decimal("74.70"): REFERENCE_BY_VARIANT["kit3"],
            Decimal("99.60"): REFERENCE_BY_VARIANT["kit4"],
            Decimal("124.50"): REFERENCE_BY_VARIANT["kit5"],
        }
        ref = by_cost.get(cost)
        if not ref:
            raise AssertionError(f"Combo de pricing inesperado no mock: cost={cost} shipping={shipping}")

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
    # Valor esperado pela referência visual.
    if decision_cost == Decimal("99.60"):
        return 19.31
    if decision_cost in {Decimal("149.40"), Decimal("199.20"), Decimal("249.00")}:
        return 22.45
    return 0.0


@pytest.mark.skipif(sync_playwright is None, reason="playwright nao instalado no ambiente")
def test_newgd60c7_reference_values_stay_exact_across_variant_navigation_and_refresh():
    root_dir = Path(__file__).resolve().parents[2]
    products = _load_tiny_fixture()
    fake_db = {}
    load_sources = []

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
                        load_sources.append("db")
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
                    load_sources.append("tiny")
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
                page.click(f"button.variant-tab-btn[data-variant='{variant_key}']")
                page.wait_for_function(
                    "(target) => (typeof activeVariantKey !== 'undefined' ? activeVariantKey : '') === target",
                    arg=variant_key,
                )
                page.wait_for_function("() => (typeof variantSwitchInProgress === 'undefined') || !variantSwitchInProgress")
                page.wait_for_function("() => (document.querySelector('#tinyPromoPriceMin').value || '').trim().length > 0")
                page.wait_for_function("() => document.querySelectorAll('#wholesalePriceBody tr').length === 3")

            def assert_variant_matches_reference(variant_key: str, step_label: str):
                ref = REFERENCE_BY_VARIANT[variant_key]

                base = ref["base"]
                expected_gtin = str(base["gtin"] or "").strip()
                got_gtin = read_input("#tinyGTIN")
                if expected_gtin:
                    _assert_exact_decimal(got_gtin, expected_gtin, f"{step_label} gtin")
                else:
                    assert got_gtin == "", f"{step_label} gtin: esperado vazio, recebido {got_gtin!r}"
                assert read_input("#tinySKUDisplay").upper() == base["sku"], (
                    f"{step_label} sku: esperado {base['sku']}, recebido {read_input('#tinySKUDisplay')}"
                )
                _assert_exact_decimal(read_input("#heightCm"), base["height"], f"{step_label} altura")
                _assert_exact_decimal(read_input("#widthCm"), base["width"], f"{step_label} largura")
                _assert_exact_decimal(read_input("#lengthCm"), base["length"], f"{step_label} comprimento")
                _assert_exact_decimal(read_input("#weightKg"), base["weight"], f"{step_label} peso")
                _assert_exact_decimal(read_input("#tinyCostPrice"), base["cost"], f"{step_label} custo")
                _assert_exact_decimal(read_input("#tinyShippingCost"), base["shipping"], f"{step_label} frete")

                announce = read_price_block("tinyAnnouncePriceMin")
                _assert_exact_decimal(announce["price"], ref["announce_min"]["price"], f"{step_label} anuncio/preco")
                _assert_exact_decimal(announce["margin"], ref["announce_min"]["margin"], f"{step_label} anuncio/margem")
                _assert_exact_decimal(announce["multiple"], ref["announce_min"]["multiple"], f"{step_label} anuncio/multiplo")
                _assert_exact_decimal(announce["value"], ref["announce_min"]["value"], f"{step_label} anuncio/valor")

                aggressive = read_price_block("tinyAggressivePriceMin")
                _assert_exact_decimal(aggressive["price"], ref["aggressive_min"]["price"], f"{step_label} agressivo/preco")
                _assert_exact_decimal(aggressive["margin"], ref["aggressive_min"]["margin"], f"{step_label} agressivo/margem")
                _assert_exact_decimal(aggressive["multiple"], ref["aggressive_min"]["multiple"], f"{step_label} agressivo/multiplo")
                _assert_exact_decimal(aggressive["value"], ref["aggressive_min"]["value"], f"{step_label} agressivo/valor")

                promo = read_price_block("tinyPromoPriceMin")
                _assert_exact_decimal(promo["price"], ref["promo_min"]["price"], f"{step_label} promo/preco")
                _assert_exact_decimal(promo["margin"], ref["promo_min"]["margin"], f"{step_label} promo/margem")
                _assert_exact_decimal(promo["multiple"], ref["promo_min"]["multiple"], f"{step_label} promo/multiplo")
                _assert_exact_decimal(promo["value"], ref["promo_min"]["value"], f"{step_label} promo/valor")

                rows = read_wholesale_rows()
                assert len(rows) == len(ref["wholesale"]), (
                    f"{step_label} atacado: esperado {len(ref['wholesale'])} linhas, recebido {len(rows)}"
                )
                for idx, expected_row in enumerate(ref["wholesale"]):
                    got = rows[idx]
                    _assert_exact_decimal(got["price"], expected_row["price"], f"{step_label} atacado[{idx}] preco")
                    _assert_exact_decimal(got["qty"], expected_row["qty"], f"{step_label} atacado[{idx}] qtd")
                    _assert_exact_decimal(got["margin"], expected_row["margin"], f"{step_label} atacado[{idx}] margem")
                    _assert_exact_decimal(got["multiple"], expected_row["multiple"], f"{step_label} atacado[{idx}] multiplo")
                    _assert_exact_decimal(got["value"], expected_row["value"], f"{step_label} atacado[{idx}] valor")

            def run_full_sequence(run_label: str):
                page.fill("#tinySKU", SKU)
                page.press("#tinySKU", "Enter")
                page.wait_for_function(
                    "(target) => document.querySelector('#tinySKUDisplay').value.toUpperCase().includes(target)",
                    arg=SKU,
                )
                page.wait_for_function("() => (document.querySelector('#tinyPromoPriceMin').value || '').trim().length > 0")
                page.wait_for_function("() => document.querySelectorAll('#wholesalePriceBody tr').length === 3")

                for step_idx, variant_key in enumerate(TAB_SEQUENCE, start=1):
                    switch_variant(variant_key)
                    assert_variant_matches_reference(
                        variant_key,
                        f"{run_label}/passo-{step_idx}/{variant_key}",
                    )

            page.goto(f"{base_url}/static/main.html", wait_until="domcontentloaded")
            page.wait_for_function("() => document.querySelector('#tinyInstance option[value=\"0\"]') !== null")
            page.select_option("#tinyInstance", "0")

            # Execução 1: dados vindos do Tiny fake
            run_full_sequence("run1")

            # F5 sem reiniciar servidor
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("() => document.querySelector('#tinyInstance option[value=\"0\"]') !== null")
            page.select_option("#tinyInstance", "0")

            # Execução 2: dados vindos do DB fake (workspace salvo em memória)
            run_full_sequence("run2")

            assert load_sources, "Nenhum carregamento de workspace foi registrado"
            assert load_sources[0] == "tiny", f"Primeiro carregamento deveria ser tiny, veio {load_sources[0]!r}"
            assert any(source == "db" for source in load_sources[1:]), (
                f"Esperado ao menos um DB hit após F5, fontes registradas: {load_sources}"
            )

            browser.close()
