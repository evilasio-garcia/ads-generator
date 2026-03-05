import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import tiny_service


def test_validate_kit_structure_sums_repeated_base_items():
    product_full = {
        "classe_produto": "K",
        "estrutura": [
            {"item": {"codigo": "SKU001", "quantidade": "1"}},
            {"item": {"codigo": "sku001", "quantidade": "1"}},
        ],
    }

    result = asyncio.run(
        tiny_service.validate_kit_structure(
            token="token",
            product_full=product_full,
            base_sku="SKU001",
            expected_quantity=2,
        )
    )

    assert result["is_valid"] is True
    assert result["is_kit_class"] is True
    assert result["only_base_sku"] is True
    assert result["quantity_matches"] is True
    assert result["total_component_qty"] == 2.0


def test_validate_kit_structure_rejects_false_positive_not_kit_class():
    product_full = {
        "classe_produto": "P",
        "estrutura": [
            {"item": {"codigo": "SKU001", "quantidade": "2"}},
        ],
    }

    result = asyncio.run(
        tiny_service.validate_kit_structure(
            token="token",
            product_full=product_full,
            base_sku="SKU001",
            expected_quantity=2,
        )
    )

    assert result["is_valid"] is False
    assert result["is_kit_class"] is False
    assert result["only_base_sku"] is True
    assert result["quantity_matches"] is True


def test_resolve_kit_candidate_returns_missing_when_only_false_positive(monkeypatch):
    async def fake_get_product_full_by_code_exact(token, code, timeout=15.0):
        if code == "SKU001CB2":
            return {
                "classe_produto": "P",
                "estrutura": [{"item": {"codigo": "SKU001", "quantidade": "2"}}],
            }
        return None

    monkeypatch.setattr(tiny_service, "_get_product_full_by_code_exact", fake_get_product_full_by_code_exact)

    result = asyncio.run(tiny_service.resolve_kit_candidate("token", "SKU001", 2))

    assert result["status"] == "missing"
    assert result["resolved_sku"] is None
    assert result["create_available"] is True
    assert result["searched_candidates"] == ["SKU001CB2", "SKU001-CB2"]
    assert result["validation"] is not None
    assert result["validation"]["is_valid"] is False


def test_build_tiny_kit_payload_exact_shape():
    payload = tiny_service._build_tiny_kit_payload(
        base_product_full={
            "id": "12345",
            "nome": "PRODUTO BASE",
            "unidade": "PCT",
            "origem": "0",
            "ncm": "2309.10.00",
            "situacao": "A",
            "tipo": "P",
            "preco": "12.50",
            "preco_promocional": "10.00",
        },
        base_sku="SKU001",
        kit_quantity=3,
        unit_plural="PACOTES",
    )

    assert payload == {
        "nome": "COMBO COM 3 PACOTES: PRODUTO BASE",
        "codigo": "SKU001CB3",
        "gtin": "",
        "classe_produto": "K",
        "preco": 37.5,
        "preco_promocional": 0.0,
        "peso_bruto": 0.0,
        "peso_liquido": 0.0,
        "volumes": 1,
        "altura_embalagem": 0.0,
        "largura_embalagem": 0.0,
        "comprimento_embalagem": 0.0,
        "kit": [{"item": {"id_produto": "12345", "quantidade": 3}}],
        "estrutura": [
            {"item": {"id_produto": "12345", "codigo": "SKU001", "descricao": "PRODUTO BASE", "quantidade": 3}}
        ],
        "unidade": "PCT",
        "origem": "0",
        "ncm": "2309.10.00",
        "situacao": "A",
        "tipo": "P",
    }


def test_build_tiny_kit_payload_applies_server_side_name_replacements():
    payload = tiny_service._build_tiny_kit_payload(
        base_product_full={
            "id": "12345",
            "nome": "RACAO C/ FRANGO PCT 10KG",
            "unidade": "PCT",
            "origem": "0",
            "situacao": "A",
            "tipo": "P",
            "preco": "12.50",
        },
        base_sku="SKU001",
        kit_quantity=2,
        unit_plural="PACOTES",
        kit_name_replacements=[
            {"from": " C/ ", "to": " COM "},
            {"from": " PCT ", "to": " PACOTE "},
        ],
    )

    assert payload["nome"] == "COMBO COM 2 PACOTES: RACAO COM FRANGO PACOTE 10KG"


def test_suggest_kit_name_uses_server_side_single_rule_point(monkeypatch):
    async def fake_get_product_full_by_code_exact(token, code, timeout=15.0):
        if code == "SKU001":
            return {"id": "123", "nome": "RACAO C/ FRANGO PCT 10KG", "unidade": "PCT"}
        return None

    monkeypatch.setattr(tiny_service, "_get_product_full_by_code_exact", fake_get_product_full_by_code_exact)

    suggestion = asyncio.run(
        tiny_service.suggest_kit_name(
            token="token",
            base_sku="SKU001",
            kit_quantity=2,
            kit_name_replacements=[
                {"from": " C/ ", "to": " COM "},
                {"from": " PCT ", "to": " PACOTE "},
            ],
        )
    )

    assert suggestion["unit_plural"] == "PACOTES"
    assert suggestion["combo_name"] == "COMBO COM 2 PACOTES: RACAO COM FRANGO PACOTE 10KG"


@pytest.mark.parametrize(
    ("kit_quantity", "expected_name"),
    [
        (2, "COMBO COM 2 PACOTES: NEW GOOD 60X60 - 7 UNIDADES"),
        (3, "COMBO COM 3 PACOTES: NEW GOOD 60X60 - 7 UNIDADES"),
        (4, "COMBO COM 4 PACOTES: NEW GOOD 60X60 - 7 UNIDADES"),
        (5, "COMBO COM 5 PACOTES: NEW GOOD 60X60 - 7 UNIDADES"),
    ],
)
def test_suggest_kit_name_newgd60c7_kits_2_to_5(monkeypatch, kit_quantity, expected_name):
    async def fake_get_product_full_by_code_exact(token, code, timeout=15.0):
        if code == "NEWGD60C7":
            return {
                "id": "321",
                "nome": "NEW GOOD 60X60 - 7 UNIDADES",
                "unidade": "PCT",
            }
        return None

    monkeypatch.setattr(tiny_service, "_get_product_full_by_code_exact", fake_get_product_full_by_code_exact)

    suggestion = asyncio.run(
        tiny_service.suggest_kit_name(
            token="token",
            base_sku="NEWGD60C7",
            kit_quantity=kit_quantity,
        )
    )

    assert suggestion["unit_plural"] == "PACOTES"
    assert suggestion["combo_name"] == expected_name


def test_create_kit_product_requires_unit_when_not_inferable(monkeypatch):
    async def fake_get_product_full_by_code_exact(token, code, timeout=15.0):
        if code == "SKU001CB2":
            return None
        if code == "SKU001":
            return {
                "id": "123",
                "nome": "Produto Base",
                # intentionally no unit fields
            }
        return None

    monkeypatch.setattr(tiny_service, "_get_product_full_by_code_exact", fake_get_product_full_by_code_exact)

    with pytest.raises(tiny_service.TinyValidationError) as exc:
        asyncio.run(tiny_service.create_kit_product("token", "SKU001", 2))

    assert exc.value.code == "unit_required"


def test_create_kit_product_returns_conflict_when_sku_exists(monkeypatch):
    async def fake_get_product_full_by_code_exact(token, code, timeout=15.0):
        if code == "SKU001CB2":
            return {"id": "already", "codigo": "SKU001CB2"}
        return None

    monkeypatch.setattr(tiny_service, "_get_product_full_by_code_exact", fake_get_product_full_by_code_exact)

    with pytest.raises(tiny_service.TinyConflictError):
        asyncio.run(tiny_service.create_kit_product("token", "SKU001", 2))


def test_create_kit_product_sends_expected_include_payload(monkeypatch):
    calls = {"get_by_code": 0, "include_payload": None}

    async def fake_get_product_full_by_code_exact(token, code, timeout=15.0):
        calls["get_by_code"] += 1
        if code == "SKU001CB2":
            return None
        if code == "SKU001":
            return {"id": "123", "nome": "Produto Base", "unidade": "PCT", "preco": "10.00", "preco_promocional": "9.00"}
        return None

    async def fake_call_tiny_api(url, payload, timeout=15.0, max_retries=2):
        calls["include_payload"] = payload
        return {
            "retorno": {
                "status": "OK",
                "registros": [
                    {"registro": {"sequencia": "1", "status": "OK", "id": "900"}}
                ],
            }
        }

    monkeypatch.setattr(tiny_service, "_get_product_full_by_code_exact", fake_get_product_full_by_code_exact)
    monkeypatch.setattr(tiny_service, "_call_tiny_api", fake_call_tiny_api)

    result = asyncio.run(tiny_service.create_kit_product("token", "SKU001", 2))

    assert result["resolved_sku"] == "SKU001CB2"
    assert result["tiny_product_id"] == "900"
    include_payload = calls["include_payload"]
    assert include_payload is not None
    assert include_payload["token"] == "token"
    assert include_payload["formato"] == "JSON"

    include_layout = json.loads(include_payload["produto"])
    assert "produtos" in include_layout and isinstance(include_layout["produtos"], list)
    assert len(include_layout["produtos"]) == 1
    product_payload = include_layout["produtos"][0]["produto"]
    assert product_payload["sequencia"] == "1"
    assert product_payload["nome"] == "COMBO COM 2 PACOTES: Produto Base"
    assert product_payload["codigo"] == "SKU001CB2"
    assert product_payload["classe_produto"] == "K"
    assert product_payload["unidade"] == "PCT"
    assert product_payload["origem"] == "0"
    assert product_payload["situacao"] == "A"
    assert product_payload["tipo"] == "P"
    assert product_payload["preco"] == 20.0
    assert product_payload["preco_promocional"] == 0.0
    assert product_payload["peso_bruto"] == 0.0
    assert product_payload["peso_liquido"] == 0.0
    assert product_payload["volumes"] == 1
    assert product_payload["altura_embalagem"] == 0.0
    assert product_payload["largura_embalagem"] == 0.0
    assert product_payload["comprimento_embalagem"] == 0.0
    assert product_payload["kit"] == [{"item": {"id_produto": "123", "quantidade": 2}}]
    assert product_payload["estrutura"] == [
        {"item": {"id_produto": "123", "codigo": "SKU001", "descricao": "Produto Base", "quantidade": 2}}
    ]


def test_get_product_full_by_code_exact_returns_none_when_search_not_found(monkeypatch):
    async def fake_search_products_by_term(token, term, timeout=15.0):
        raise tiny_service.TinyNotFoundError("A consulta nao retornou registros")

    monkeypatch.setattr(tiny_service, "_search_products_by_term", fake_search_products_by_term)

    result = asyncio.run(tiny_service._get_product_full_by_code_exact("token", "SKU001CB2"))
    assert result is None


def test_create_kit_product_continues_when_combo_lookup_returns_not_found(monkeypatch):
    state = {"created": False}

    async def fake_search_products_by_term(token, term, timeout=15.0):
        if term == "SKU001CB2":
            if not state["created"]:
                raise tiny_service.TinyNotFoundError("A consulta nao retornou registros")
            return [{"codigo": "SKU001CB2", "id": "900"}]
        if term == "SKU001":
            return [{"codigo": "SKU001", "id": "123"}]
        return []

    async def fake_get_product_full_by_id(token, product_id, timeout=15.0):
        pid = str(product_id)
        if pid == "123":
            return {"id": "123", "codigo": "SKU001", "nome": "Produto Base", "unidade": "PCT"}
        if pid == "900":
            return {
                "id": "900",
                "codigo": "SKU001CB2",
                "classe_produto": "K",
                "estrutura": [{"item": {"codigo": "SKU001", "quantidade": "2"}}],
            }
        raise tiny_service.TinyNotFoundError(f"Produto id={pid} nao encontrado")

    async def fake_call_tiny_api(url, payload, timeout=15.0, max_retries=2):
        state["created"] = True
        return {"retorno": {"status": "OK"}}

    async def fake_validate_kit_structure(token, product_full, base_sku, expected_quantity, timeout=15.0):
        return {
            "is_valid": True,
            "is_kit_class": True,
            "only_base_sku": True,
            "quantity_matches": True,
            "total_component_qty": 2.0,
            "component_skus": ["SKU001"],
        }

    monkeypatch.setattr(tiny_service, "_search_products_by_term", fake_search_products_by_term)
    monkeypatch.setattr(tiny_service, "_get_product_full_by_id", fake_get_product_full_by_id)
    monkeypatch.setattr(tiny_service, "_call_tiny_api", fake_call_tiny_api)
    monkeypatch.setattr(tiny_service, "validate_kit_structure", fake_validate_kit_structure)

    result = asyncio.run(tiny_service.create_kit_product("token", "SKU001", 2))
    assert result["resolved_sku"] == "SKU001CB2"
    assert state["created"] is True


def test_create_kit_product_accepts_ok_without_registros(monkeypatch):
    state = {"created": False}

    async def fake_get_product_full_by_code_exact(token, code, timeout=15.0):
        if code == "SKU001CB2" and not state["created"]:
            return None
        if code == "SKU001CB2" and state["created"]:
            return {"id": "900", "codigo": "SKU001CB2", "classe_produto": "K"}
        if code == "SKU001":
            return {"id": "123", "nome": "Produto Base", "unidade": "PCT"}
        return None

    async def fake_call_tiny_api(_url, _payload, timeout=15.0, max_retries=2):
        state["created"] = True
        return {"retorno": {"status": "OK", "registros": []}}

    monkeypatch.setattr(tiny_service, "_get_product_full_by_code_exact", fake_get_product_full_by_code_exact)
    monkeypatch.setattr(tiny_service, "_call_tiny_api", fake_call_tiny_api)

    result = asyncio.run(tiny_service.create_kit_product("token", "SKU001", 2))
    assert result["resolved_sku"] == "SKU001CB2"
    assert result["tiny_product_id"] == "900"
    assert state["created"] is True


def test_create_kit_product_raises_when_ok_without_registros_but_confirm_fails(monkeypatch):
    state = {"created": False}

    async def fake_get_product_full_by_code_exact(token, code, timeout=15.0):
        if code == "SKU001CB2":
            return None
        if code == "SKU001":
            return {"id": "123", "nome": "Produto Base", "unidade": "PCT"}
        return None

    async def fake_call_tiny_api(_url, _payload, timeout=15.0, max_retries=2):
        state["created"] = True
        return {"retorno": {"status": "OK", "registros": []}}

    monkeypatch.setattr(tiny_service, "_get_product_full_by_code_exact", fake_get_product_full_by_code_exact)
    monkeypatch.setattr(tiny_service, "_call_tiny_api", fake_call_tiny_api)

    with pytest.raises(tiny_service.TinyServiceError):
        asyncio.run(tiny_service.create_kit_product("token", "SKU001", 2))


def test_create_kit_product_uses_combo_name_override(monkeypatch):
    calls = {"include_payload": None, "created": False}

    async def fake_get_product_full_by_code_exact(token, code, timeout=15.0):
        if code == "SKU001CB2" and not calls["created"]:
            return None
        if code == "SKU001CB2" and calls["created"]:
            return {"id": "901", "codigo": "SKU001CB2", "classe_produto": "K"}
        if code == "SKU001":
            return {"id": "123", "nome": "Produto Base", "unidade": "PCT"}
        return None

    async def fake_call_tiny_api(url, payload, timeout=15.0, max_retries=2):
        calls["include_payload"] = payload
        calls["created"] = True
        return {"retorno": {"status": "OK", "registros": []}}

    monkeypatch.setattr(tiny_service, "_get_product_full_by_code_exact", fake_get_product_full_by_code_exact)
    monkeypatch.setattr(tiny_service, "_call_tiny_api", fake_call_tiny_api)

    override_name = "COMBO COM 2 UNIDADES: Produto Base Especial"
    asyncio.run(
        tiny_service.create_kit_product(
            "token",
            "SKU001",
            2,
            combo_name_override=override_name,
        )
    )

    include_payload = calls["include_payload"]
    assert include_payload is not None
    include_layout = json.loads(include_payload["produto"])
    product_payload = include_layout["produtos"][0]["produto"]
    assert product_payload["nome"] == override_name


def test_create_kit_product_raises_when_include_record_status_is_error(monkeypatch):
    async def fake_get_product_full_by_code_exact(token, code, timeout=15.0):
        if code == "SKU001CB2":
            return None
        if code == "SKU001":
            return {"id": "123", "nome": "Produto Base", "unidade": "PCT"}
        return None

    async def fake_call_tiny_api(url, payload, timeout=15.0, max_retries=2):
        return {
            "retorno": {
                "status": "OK",
                "registros": [
                    {
                        "registro": {
                            "sequencia": "1",
                            "status": "Erro",
                            "erros": [{"erro": "Ja cadastrado"}],
                        }
                    }
                ],
            }
        }

    monkeypatch.setattr(tiny_service, "_get_product_full_by_code_exact", fake_get_product_full_by_code_exact)
    monkeypatch.setattr(tiny_service, "_call_tiny_api", fake_call_tiny_api)

    with pytest.raises(tiny_service.TinyConflictError):
        asyncio.run(tiny_service.create_kit_product("token", "SKU001", 2))


def test_build_tiny_kit_payload_applies_ads_gen_context_fields():
    payload = tiny_service._build_tiny_kit_payload(
        base_product_full={
            "id": "12345",
            "nome": "PRODUTO BASE",
            "unidade": "PCT",
            "origem": "0",
            "situacao": "A",
            "tipo": "P",
            "preco": "10.00",
            "preco_promocional": "9.00",
        },
        base_sku="SKU001",
        kit_quantity=2,
        unit_plural="PACOTES",
        announcement_price=141.42,
        promotional_price=0.0,
        base_unit_override="CX",
        kit_weight_kg=0.8,
        kit_height_cm=13,
        kit_width_cm=22,
        kit_length_cm=18,
        kit_volumes=1,
        kit_description="Descricao da aba kit 2",
    )

    assert payload["preco"] == 141.42
    assert payload["preco_promocional"] == 0.0
    assert payload["unidade"] == "CX"
    assert payload["peso_bruto"] == 0.8
    assert payload["peso_liquido"] == 0.8
    assert payload["altura_embalagem"] == 13.0
    assert payload["largura_embalagem"] == 22.0
    assert payload["comprimento_embalagem"] == 18.0
    assert payload["volumes"] == 1
    assert payload["descricao_complementar"] == "Descricao da aba kit 2"


def test_create_kit_product_retries_with_announcement_price_when_promo_zero_is_rejected(monkeypatch):
    calls = {"include": 0}

    async def fake_get_product_full_by_code_exact(token, code, timeout=15.0):
        if code == "SKU001CB2":
            return {"id": "900", "codigo": "SKU001CB2", "classe_produto": "K"} if calls["include"] >= 2 else None
        if code == "SKU001":
            return {"id": "123", "nome": "Produto Base", "unidade": "PCT"}
        return None

    async def fake_call_tiny_api(url, payload, timeout=15.0, max_retries=2):
        calls["include"] += 1
        include_layout = json.loads(payload["produto"])
        product_payload = include_layout["produtos"][0]["produto"]
        if calls["include"] == 1:
            assert product_payload["preco_promocional"] == 0.0
            return {
                "retorno": {
                    "status": "Erro",
                    "erros": [{"erro": "O preco promocional deve ser informado"}],
                }
            }
        assert product_payload["preco_promocional"] == 141.42
        return {
            "retorno": {
                "status": "OK",
                "registros": [],
            }
        }

    monkeypatch.setattr(tiny_service, "_get_product_full_by_code_exact", fake_get_product_full_by_code_exact)
    monkeypatch.setattr(tiny_service, "_call_tiny_api", fake_call_tiny_api)

    result = asyncio.run(
        tiny_service.create_kit_product(
            "token",
            "SKU001",
            2,
            announcement_price=141.42,
            promotional_price=0.0,
        )
    )
    assert result["resolved_sku"] == "SKU001CB2"
    assert result["tiny_product_id"] == "900"
    assert calls["include"] == 2


def test_create_kit_product_retries_post_include_confirmation_before_failing(monkeypatch):
    state = {"created": False, "confirm_calls": 0}

    async def fake_get_product_full_by_code_exact(token, code, timeout=15.0):
        if code == "SKU001CB2":
            if not state["created"]:
                return None
            state["confirm_calls"] += 1
            if state["confirm_calls"] < 3:
                return None
            return {"id": "900", "codigo": "SKU001CB2", "classe_produto": "K"}
        if code == "SKU001":
            return {"id": "123", "nome": "Produto Base", "unidade": "PCT"}
        return None

    async def fake_call_tiny_api(_url, _payload, timeout=15.0, max_retries=2):
        state["created"] = True
        return {"retorno": {"status": "OK", "registros": []}}

    async def fast_sleep(_delay):
        return None

    monkeypatch.setattr(tiny_service, "_get_product_full_by_code_exact", fake_get_product_full_by_code_exact)
    monkeypatch.setattr(tiny_service, "_call_tiny_api", fake_call_tiny_api)
    monkeypatch.setattr(tiny_service.asyncio, "sleep", fast_sleep)

    result = asyncio.run(tiny_service.create_kit_product("token", "SKU001", 2))
    assert result["tiny_product_id"] == "900"
    assert state["confirm_calls"] == 3
