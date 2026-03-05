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

SKU = "NEWGD60C7"


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


def _attach_common_mocks(context):
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

        return route.continue_()

    context.route("**/*", handle_routes)


def _goto_main_page(page, base_url: str):
    page.goto(f"{base_url}/static/main.html", wait_until="domcontentloaded")
    page.wait_for_selector("#btnConfig")
    page.wait_for_selector("button.info-popover-trigger[data-info-template='#infoTemplateSkuBehavior']")
    page.wait_for_function("() => typeof updateTinyInstanceSelect === 'function'")
    page.evaluate(
        """
        () => {
            appConfig = appConfig || {};
            appConfig.tiny_tokens = [{ label: "Tiny SP", token: "token-sp" }];
            appConfig.pricing_config = [
                {
                    marketplace: "mercadolivre",
                    comissao_min: 12,
                    comissao_max: 17,
                    tacos: 5,
                    margem_contribuicao: 15,
                    lucro: 10,
                    impostos: 8
                }
            ];
            updateTinyInstanceSelect();
        }
        """
    )
    page.wait_for_function("() => document.querySelector('#tinyInstance option[value=\"0\"]') !== null")


def test_gui_tooltip_combobox_and_config_are_accessible():
    root_dir = Path(__file__).resolve().parents[2]
    with _static_server(root_dir) as base_url:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:  # pragma: no cover - env without browser binaries
                pytest.skip(f"Chromium indisponivel no ambiente: {exc}")

            context = browser.new_context(viewport={"width": 1440, "height": 1100})
            _attach_common_mocks(context)
            page = context.new_page()

            _goto_main_page(page, base_url)

            tooltip_trigger = "button.info-popover-trigger[data-info-template='#infoTemplateSkuBehavior']"
            assert page.get_attribute(tooltip_trigger, "aria-label"), "Tooltip trigger deve expor aria-label"
            assert page.get_attribute(tooltip_trigger, "aria-expanded") == "false"

            page.click(tooltip_trigger)
            page.wait_for_function(
                """
                () => {
                    const pop = document.querySelector('#infoPopover');
                    return !!pop && pop.hidden === false;
                }
                """
            )
            tooltip_state = page.evaluate(
                """
                () => {
                    const trigger = document.querySelector("button.info-popover-trigger[data-info-template='#infoTemplateSkuBehavior']");
                    const pop = document.querySelector('#infoPopover');
                    const title = document.querySelector('#infoPopoverTitle');
                    const content = document.querySelector('#infoPopoverContent');
                    return {
                        hidden: pop ? pop.hidden : true,
                        expanded: trigger ? trigger.getAttribute('aria-expanded') : null,
                        title: title ? title.textContent.trim() : "",
                        content_length: content ? content.textContent.trim().length : 0
                    };
                }
                """
            )
            assert tooltip_state["hidden"] is False, "Popover de informacao deve abrir ao clicar no tooltip"
            assert tooltip_state["expanded"] == "true", "Tooltip precisa marcar aria-expanded=true quando aberto"
            assert tooltip_state["title"], "Popover deve ter titulo renderizado"
            assert tooltip_state["content_length"] > 20, "Popover deve conter texto util para o usuario"

            page.click("#infoPopoverClose")
            page.wait_for_function(
                """
                () => {
                    const pop = document.querySelector('#infoPopover');
                    return !!pop && pop.hidden === true;
                }
                """
            )
            assert page.get_attribute(tooltip_trigger, "aria-expanded") == "false"

            combo_state = page.evaluate(
                """
                () => {
                    const combo = document.querySelector('#tinyInstance');
                    return {
                        disabled: combo ? combo.disabled : true,
                        options: combo ? combo.querySelectorAll('option').length : 0
                    };
                }
                """
            )
            assert combo_state["disabled"] is False, "Combobox de origem Tiny deve estar habilitado"
            assert combo_state["options"] >= 2, "Combobox de origem Tiny deve ter opcoes carregadas"

            page.select_option("#tinyInstance", "0")
            page.wait_for_function("() => document.querySelector('#tinyInstance').value === '0'")
            page.wait_for_function("() => document.querySelector('#tinySKU').disabled === false")

            page.fill("#tinySKU", SKU)
            page.wait_for_function("() => document.querySelector('#btnTinySearch').disabled === false")

            assert page.input_value("#tinyInstance") == "0"
            assert page.get_attribute("#btnConfig", "title"), "Botao de Config deve ter tooltip title"

            page.click("#btnConfig")
            page.wait_for_selector("#configModal.open")

            modal_state = page.evaluate(
                """
                () => {
                    const modal = document.querySelector('#configModal');
                    const llmSection = document.querySelector('.cfg-section[data-section="llm"]');
                    return {
                        is_open: !!modal && modal.classList.contains('open'),
                        llm_visible: !!llmSection && getComputedStyle(llmSection).display !== 'none'
                    };
                }
                """
            )
            assert modal_state["is_open"], "Modal de config precisa abrir ao clicar em Config"
            assert modal_state["llm_visible"], "Modal deve abrir com secao LLM visivel"

            page.click(".cfg-nav-btn[data-section='pricing']")
            page.wait_for_function(
                """
                () => {
                    const section = document.querySelector('.cfg-section[data-section="pricing"]');
                    return !!section && getComputedStyle(section).display !== 'none';
                }
                """
            )
            page.wait_for_selector("#cfgPricingTableBody select[data-field='marketplace']")

            pricing_select = "#cfgPricingTableBody select[data-field='marketplace']"
            page.select_option(pricing_select, "shopee")
            assert page.input_value(pricing_select) == "shopee"

            page.click("#cfgClose")
            page.wait_for_function(
                """
                () => {
                    const modal = document.querySelector('#configModal');
                    return !!modal && !modal.classList.contains('open');
                }
                """
            )

            browser.close()
