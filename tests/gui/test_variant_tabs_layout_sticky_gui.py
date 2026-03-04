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

        if path == "/api/sku/workspace/save" and method == "POST":
            return _json_response(
                route,
                {
                    "ok": True,
                    "saved": True,
                    "workspace_id": "ws-test",
                    "history_id": "h-1",
                    "reason": None,
                },
            )

        if path == "/api/sku/workspace/load" and method == "POST":
            return _json_response(
                route,
                {"detail": {"message": "SKU nao carregado neste teste", "type": "not_found"}},
                status=404,
            )

        if path == "/pricing/quote" and method == "POST":
            return _json_response(
                route,
                {
                    "announce_price": 0,
                    "aggressive_price": 0,
                    "promo_price": 0,
                    "wholesale_prices": [],
                },
            )

        if path == "/api/shipping/calculate_ml" and method == "POST":
            return _json_response(route, {"shipping_cost": 0})

        if path == "/api/canva/list" and method == "POST":
            return _json_response(route, {"design": None})

        return route.continue_()

    context.route("**/*", handle_routes)


def _goto_main_page(page, base_url: str):
    page.goto(f"{base_url}/static/main.html", wait_until="domcontentloaded")
    page.wait_for_function("() => document.querySelectorAll('#variantTabs .variant-tab-btn').length === 5")
    page.wait_for_function("() => document.querySelector('#tinyInstance option[value=\"0\"]') !== null")
    page.select_option("#tinyInstance", "0")


def test_variant_tabs_ear_layout_sticky_and_mobile_fallback_gui():
    root_dir = Path(__file__).resolve().parents[2]
    with _static_server(root_dir) as base_url:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:  # pragma: no cover - env without browser binaries
                pytest.skip(f"Chromium indisponivel no ambiente: {exc}")

            context = browser.new_context(viewport={"width": 1440, "height": 1280})
            _attach_common_mocks(context)
            page = context.new_page()

            _goto_main_page(page, base_url)

            desktop_layout = page.evaluate(
                """
                () => {
                    const railInner = document.querySelector('.variant-tabs-rail-inner');
                    const tabs = document.querySelector('#variantTabs');
                    const label = document.querySelector('#variantTabs .variant-tab-label');
                    const railStyle = railInner ? getComputedStyle(railInner) : null;
                    const tabsStyle = tabs ? getComputedStyle(tabs) : null;
                    const labelStyle = label ? getComputedStyle(label) : null;
                    const tabRects = Array.from(document.querySelectorAll('#variantTabs .variant-tab-btn')).map((btn) => {
                        const lbl = btn.querySelector('.variant-tab-label');
                        const b = btn.getBoundingClientRect();
                        const l = lbl ? lbl.getBoundingClientRect() : null;
                        const hasOverflow = !!l && (l.left < b.left - 1 || l.right > b.right + 1 || l.top < b.top - 1 || l.bottom > b.bottom + 1);
                        return { hasOverflow };
                    });
                    return {
                        hasRail: !!railInner,
                        tabCount: document.querySelectorAll('#variantTabs .variant-tab-btn').length,
                        railPosition: railStyle ? railStyle.position : null,
                        tabsDirection: tabsStyle ? tabsStyle.flexDirection : null,
                        labelTransform: labelStyle ? labelStyle.transform : null,
                        anyOverflow: tabRects.some((r) => r.hasOverflow),
                    };
                }
                """
            )
            assert desktop_layout["hasRail"], "Desktop: trilho lateral das abas nao encontrado"
            assert desktop_layout["tabCount"] == 5, "Desktop: deve haver 5 abas de variacao"
            assert desktop_layout["railPosition"] == "sticky", (
                f"Desktop: esperado sticky no trilho, obtido {desktop_layout['railPosition']!r}"
            )
            assert desktop_layout["tabsDirection"] == "column", (
                f"Desktop: esperado empilhamento vertical, obtido {desktop_layout['tabsDirection']!r}"
            )
            assert desktop_layout["labelTransform"] not in (None, "none"), (
                "Desktop: label da aba deve estar rotacionada"
            )
            assert not desktop_layout["anyOverflow"], "Desktop: texto da aba vazando para fora do botao"

            page.evaluate(
                """
                () => {
                    const card = document.querySelector('#resultCard');
                    const absoluteTop = card.getBoundingClientRect().top + window.scrollY;
                    window.scrollTo(0, Math.max(0, absoluteTop + 120));
                }
                """
            )
            page.wait_for_timeout(120)

            sticky_a = page.evaluate(
                """
                () => {
                    const rail = document.querySelector('.variant-tabs-rail-inner').getBoundingClientRect();
                    return { top: rail.top, bottom: rail.bottom };
                }
                """
            )
            if float(sticky_a["top"]) > 110:
                page.evaluate(
                    "(delta) => window.scrollBy(0, Math.max(0, delta))",
                    float(sticky_a["top"]) - 88.0,
                )
                page.wait_for_timeout(120)
                sticky_a = page.evaluate(
                    """
                    () => {
                        const rail = document.querySelector('.variant-tabs-rail-inner').getBoundingClientRect();
                        return { top: rail.top, bottom: rail.bottom };
                    }
                    """
                )
            page.evaluate("() => window.scrollBy(0, 260)")
            page.wait_for_timeout(120)
            sticky_b = page.evaluate(
                """
                () => {
                    const rail = document.querySelector('.variant-tabs-rail-inner').getBoundingClientRect();
                    return { top: rail.top, bottom: rail.bottom };
                }
                """
            )
            assert abs(float(sticky_b["top"]) - float(sticky_a["top"])) <= 4.0, (
                f"Desktop sticky: top do trilho variou alem do esperado ({sticky_a['top']} -> {sticky_b['top']})"
            )

            page.evaluate(
                """
                () => {
                    const card = document.querySelector('#resultCard');
                    const absoluteBottom = card.getBoundingClientRect().bottom + window.scrollY;
                    window.scrollTo(0, Math.max(0, absoluteBottom - window.innerHeight + 24));
                }
                """
            )
            page.wait_for_timeout(120)
            bounded = page.evaluate(
                """
                () => {
                    const rail = document.querySelector('.variant-tabs-rail-inner').getBoundingClientRect();
                    const card = document.querySelector('#resultCard').getBoundingClientRect();
                    return { railBottom: rail.bottom, cardBottom: card.bottom };
                }
                """
            )
            assert float(bounded["railBottom"]) <= float(bounded["cardBottom"]) + 2.0, (
                f"Sticky bounded: trilho ultrapassou card (railBottom={bounded['railBottom']}, cardBottom={bounded['cardBottom']})"
            )

            page.click("button.variant-tab-btn[data-variant='kit5']")
            page.wait_for_function(
                "(target) => (typeof activeVariantKey !== 'undefined' ? activeVariantKey : '') === target",
                arg="kit5",
            )

            page.set_viewport_size({"width": 900, "height": 1280})
            page.wait_for_timeout(150)
            mobile_layout = page.evaluate(
                """
                () => {
                    const railInner = document.querySelector('.variant-tabs-rail-inner');
                    const tabs = document.querySelector('#variantTabs');
                    const label = document.querySelector('#variantTabs .variant-tab-label');
                    const railStyle = railInner ? getComputedStyle(railInner) : null;
                    const tabsStyle = tabs ? getComputedStyle(tabs) : null;
                    const labelStyle = label ? getComputedStyle(label) : null;
                    return {
                        railPosition: railStyle ? railStyle.position : null,
                        tabsDirection: tabsStyle ? tabsStyle.flexDirection : null,
                        labelTransform: labelStyle ? labelStyle.transform : null,
                    };
                }
                """
            )
            assert mobile_layout["railPosition"] == "static", (
                f"Mobile: trilho deve sair do sticky, obtido {mobile_layout['railPosition']!r}"
            )
            assert mobile_layout["tabsDirection"] == "row", (
                f"Mobile: abas devem virar chips horizontais, obtido {mobile_layout['tabsDirection']!r}"
            )
            assert mobile_layout["labelTransform"] in ("none", "matrix(1, 0, 0, 1, 0, 0)"), (
                f"Mobile: label nao deve manter rotacao, obtido {mobile_layout['labelTransform']!r}"
            )

            browser.close()


def test_variant_tab_switch_keeps_scroll_and_no_reload_gui():
    root_dir = Path(__file__).resolve().parents[2]
    with _static_server(root_dir) as base_url:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:  # pragma: no cover - env without browser binaries
                pytest.skip(f"Chromium indisponivel no ambiente: {exc}")

            context = browser.new_context(viewport={"width": 1440, "height": 1280})
            _attach_common_mocks(context)
            page = context.new_page()

            _goto_main_page(page, base_url)

            boot_marker = page.evaluate("() => window.__adsGeneratorBootMarker || null")
            boot_count = int(page.evaluate("() => Number(window.__adsGeneratorBootCount || 0)"))
            assert boot_marker, "Boot marker deve existir para validar ausencia de reload"
            assert boot_count >= 1, "Boot count deve iniciar em 1"

            page.evaluate(
                """
                () => {
                    const card = document.querySelector('#resultCard');
                    const absoluteTop = card.getBoundingClientRect().top + window.scrollY;
                    window.scrollTo(0, Math.max(0, absoluteTop + 180));
                }
                """
            )
            page.wait_for_timeout(120)
            baseline_scroll = float(page.evaluate("() => window.scrollY"))

            sequence = ["kit5", "kit2", "simple", "kit4", "simple"]
            for idx, variant_key in enumerate(sequence, start=1):
                page.evaluate(
                    "(selector) => { const el = document.querySelector(selector); if (el) el.click(); }",
                    f"button.variant-tab-btn[data-variant='{variant_key}']",
                )
                page.wait_for_function(
                    "(target) => (typeof activeVariantKey !== 'undefined' ? activeVariantKey : '') === target",
                    arg=variant_key,
                )
                page.wait_for_function("() => (typeof variantSwitchInProgress === 'undefined') || !variantSwitchInProgress")
                current_scroll = float(page.evaluate("() => window.scrollY"))
                assert abs(current_scroll - baseline_scroll) <= 1.0, (
                    f"Troca de aba step {idx}/{variant_key} moveu o scroll: {baseline_scroll} -> {current_scroll}"
                )

            assert page.evaluate("() => window.__adsGeneratorBootMarker || null") == boot_marker, (
                "Boot marker mudou apos troca de abas (indicio de reload)"
            )
            assert int(page.evaluate("() => Number(window.__adsGeneratorBootCount || 0)")) == boot_count, (
                "Boot count mudou apos troca de abas (indicio de reload)"
            )

            nav_entries = int(page.evaluate("() => performance.getEntriesByType('navigation').length"))
            assert nav_entries == 1, f"Nao deve haver nova navegacao/reload na troca de abas, got {nav_entries}"

            browser.close()
