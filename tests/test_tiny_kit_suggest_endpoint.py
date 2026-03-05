import asyncio
import os
import sys
import json
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app as app_module
import tiny_service
from auth_helpers import CurrentUser


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "tiny" / "tiny_sku_fixture.json"


class _FakeUserConfigRecord:
    def __init__(self, data: Optional[Dict[str, Any]] = None):
        self.data = data or {}


class _FakeQuery:
    def __init__(self, record: Optional[Any]):
        self._record = record

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._record


class _FakeDbSession:
    def __init__(self, user_config_data: Optional[Dict[str, Any]] = None):
        self._cfg_record = _FakeUserConfigRecord(user_config_data)

    def query(self, model):
        if model is app_module.UserConfig:
            return _FakeQuery(self._cfg_record)
        return _FakeQuery(None)


def _build_current_user() -> CurrentUser:
    return CurrentUser(
        user_id="test-user",
        email="test@example.com",
        raw_claims={},
    )


def _post_suggest_name(payload: Dict[str, Any]) -> httpx.Response:
    async def _run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/api/tiny/kit/suggest-name", json=payload)

    return asyncio.run(_run())


def _load_newgd60c7_fixture() -> Dict[str, Any]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    products = payload.get("products") or {}
    product = products.get("NEWGD60C7") or {}
    if not product:
        pytest.skip(f"Fixture sem NEWGD60C7: {FIXTURE_PATH}")
    return product


@pytest.mark.parametrize(
    ("kit_quantity", "expected_name"),
    [
        (2, "COMBO COM 2 PACOTES: NEW GOOD 60X60 - 7 UNIDADES"),
        (3, "COMBO COM 3 PACOTES: NEW GOOD 60X60 - 7 UNIDADES"),
        (4, "COMBO COM 4 PACOTES: NEW GOOD 60X60 - 7 UNIDADES"),
        (5, "COMBO COM 5 PACOTES: NEW GOOD 60X60 - 7 UNIDADES"),
    ],
)
def test_endpoint_suggest_name_newgd60c7_kits_2_to_5(monkeypatch, kit_quantity, expected_name):
    fixture_product = _load_newgd60c7_fixture()

    async def fake_get_product_full_by_code_exact(token, code, timeout=15.0):
        if str(code).upper() == "NEWGD60C7":
            return {
                "id": "321",
                "nome": str(fixture_product.get("title") or "NEW GOOD 60X60 - 7 UNIDADES"),
                # Cenário real reportado: unidade retornando singular.
                "unidade": "PACOTE",
            }
        return None

    monkeypatch.setattr(tiny_service, "_get_product_full_by_code_exact", fake_get_product_full_by_code_exact)

    # Regras sem espaços reproduzem regressão histórica (UNID -> UNIDADE em UNIDADES).
    # O endpoint deve manter "UNIDADES" intacto.
    fake_db = _FakeDbSession(
        {
            "general": {
                "kit_name_replacements": [
                    {"from": "UNID", "to": "UNIDADE"},
                    {"from": "PCT", "to": "PACOTE"},
                ]
            }
        }
    )

    def override_user():
        return _build_current_user()

    def override_db():
        yield fake_db

    app_module.app.dependency_overrides[app_module.get_current_user_master] = override_user
    app_module.app.dependency_overrides[app_module.get_db] = override_db
    try:
        response = _post_suggest_name(
            {
                "token": "token-fake",
                "base_sku": "NEWGD60C7",
                "kit_quantity": kit_quantity,
            }
        )
    finally:
        app_module.app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["unit_plural"] == "PACOTES"
    assert data["combo_name"] == expected_name
