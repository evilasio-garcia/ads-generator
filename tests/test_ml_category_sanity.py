# tests/test_ml_category_sanity.py
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from app import _validate_category_attributes, _humanize_ml_error


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_attrs(ids_and_tags):
    """Helper: cria lista de atributos ML a partir de [(id, tags_dict), ...]"""
    return [
        {"id": aid, "name": aid.replace("_", " ").title(), "tags": tags}
        for aid, tags in ids_and_tags
    ]


def _make_baseline(required=None, conditional=None, hidden_writable=None):
    """Helper: simula um registro de baseline."""
    return {
        "required_attr_ids": required or [],
        "conditional_attr_ids": conditional or [],
        "hidden_writable_attr_ids": hidden_writable or [],
    }


FULL_DIMS = {"height_cm": 10, "width_cm": 20, "length_cm": 30, "weight_kg": 0.5}
ZERO_DIMS = {"height_cm": 0, "width_cm": 0, "length_cm": 0, "weight_kg": 0}


# ── Cenário A: Primeira publicação, todos os required preenchidos ─────────

def test_first_publish_all_required_filled():
    ml_api_attrs = _make_attrs([
        ("BRAND", {"required": True}),
        ("MODEL", {"required": True}),
        ("COLOR", {}),
    ])
    ui_ml_attributes = [
        {"id": "BRAND", "value_name": "Nike"},
        {"id": "MODEL", "value_name": "Air Max"},
    ]
    result = _validate_category_attributes(
        ml_api_attrs=ml_api_attrs,
        baseline=None,
        ui_ml_attributes=ui_ml_attributes,
        ui_dimensions=FULL_DIMS,
    )
    assert result["status"] == "ok"
    assert result["is_first_publish"] is True
    assert set(result["new_baseline"]["required_attr_ids"]) == {"BRAND", "MODEL"}


# ── Cenário B: Baseline existe, sem mudanças ──────────────────────────────

def test_baseline_exists_no_change():
    ml_api_attrs = _make_attrs([
        ("BRAND", {"required": True}),
        ("MODEL", {"required": True}),
    ])
    baseline = _make_baseline(required=["BRAND", "MODEL"])
    result = _validate_category_attributes(
        ml_api_attrs=ml_api_attrs,
        baseline=baseline,
        ui_ml_attributes=[{"id": "BRAND", "value_name": "X"}, {"id": "MODEL", "value_name": "Y"}],
        ui_dimensions=FULL_DIMS,
    )
    assert result["status"] == "ok"
    assert result["is_first_publish"] is False
    assert result["added"] == []
    assert result["removed"] == []


# ── Cenário C: Mudança detectada, auto-resolvível (SELLER_PACKAGE_*) ─────

def test_change_detected_auto_resolvable_seller_package():
    ml_api_attrs = _make_attrs([
        ("BRAND", {"required": True}),
        ("MODEL", {"required": True}),
        ("SELLER_PACKAGE_HEIGHT", {"hidden": True}),
        ("SELLER_PACKAGE_WIDTH", {"hidden": True}),
        ("SELLER_PACKAGE_LENGTH", {"hidden": True}),
        ("SELLER_PACKAGE_WEIGHT", {"hidden": True}),
    ])
    baseline = _make_baseline(
        required=["BRAND", "MODEL"],
        hidden_writable=[],
    )
    result = _validate_category_attributes(
        ml_api_attrs=ml_api_attrs,
        baseline=baseline,
        ui_ml_attributes=[{"id": "BRAND", "value_name": "X"}, {"id": "MODEL", "value_name": "Y"}],
        ui_dimensions=FULL_DIMS,
    )
    assert result["status"] == "ok"
    assert len(result["auto_injected"]) == 4
    injected_ids = {a["id"] for a in result["auto_injected"]}
    assert injected_ids == {"SELLER_PACKAGE_HEIGHT", "SELLER_PACKAGE_WIDTH", "SELLER_PACKAGE_LENGTH", "SELLER_PACKAGE_WEIGHT"}


# ── Cenário D: Mudança detectada, NÃO resolvível (dimensões ausentes) ────

def test_change_detected_not_resolvable_missing_dimensions():
    ml_api_attrs = _make_attrs([
        ("BRAND", {"required": True}),
        ("MODEL", {"required": True}),
        ("SELLER_PACKAGE_HEIGHT", {"hidden": True}),
        ("SELLER_PACKAGE_WIDTH", {"hidden": True}),
        ("SELLER_PACKAGE_LENGTH", {"hidden": True}),
        ("SELLER_PACKAGE_WEIGHT", {"hidden": True}),
    ])
    baseline = _make_baseline(
        required=["BRAND", "MODEL"],
        hidden_writable=[],
    )
    result = _validate_category_attributes(
        ml_api_attrs=ml_api_attrs,
        baseline=baseline,
        ui_ml_attributes=[{"id": "BRAND", "value_name": "X"}, {"id": "MODEL", "value_name": "Y"}],
        ui_dimensions=ZERO_DIMS,
    )
    assert result["status"] == "error"
    assert len(result["missing_attrs"]) == 4


# ── Cenário E: Required removido do ML ────────────────────────────────────

def test_required_removed_from_category():
    ml_api_attrs = _make_attrs([
        ("BRAND", {"required": True}),
    ])
    baseline = _make_baseline(required=["BRAND", "MODEL"])
    result = _validate_category_attributes(
        ml_api_attrs=ml_api_attrs,
        baseline=baseline,
        ui_ml_attributes=[{"id": "BRAND", "value_name": "X"}],
        ui_dimensions=FULL_DIMS,
    )
    assert result["status"] == "ok"
    assert "MODEL" in result["removed"]


# ── Cenário F: Novo required adicionado, UI já tem o valor ───────────────

def test_new_required_already_in_ui():
    ml_api_attrs = _make_attrs([
        ("BRAND", {"required": True}),
        ("MODEL", {"required": True}),
        ("GTIN", {"required": True}),
    ])
    baseline = _make_baseline(required=["BRAND", "MODEL"])
    result = _validate_category_attributes(
        ml_api_attrs=ml_api_attrs,
        baseline=baseline,
        ui_ml_attributes=[
            {"id": "BRAND", "value_name": "X"},
            {"id": "MODEL", "value_name": "Y"},
            {"id": "GTIN", "value_name": "1234567890123"},
        ],
        ui_dimensions=FULL_DIMS,
    )
    assert result["status"] == "ok"
    assert "GTIN" in result["added"]


# ── Cenário G: Novo required adicionado, UI NÃO tem o valor ──────────────

def test_new_required_missing_from_ui():
    ml_api_attrs = _make_attrs([
        ("BRAND", {"required": True}),
        ("MODEL", {"required": True}),
        ("GTIN", {"required": True}),
    ])
    baseline = _make_baseline(required=["BRAND", "MODEL"])
    result = _validate_category_attributes(
        ml_api_attrs=ml_api_attrs,
        baseline=baseline,
        ui_ml_attributes=[
            {"id": "BRAND", "value_name": "X"},
            {"id": "MODEL", "value_name": "Y"},
        ],
        ui_dimensions=FULL_DIMS,
    )
    assert result["status"] == "error"
    assert any(a["id"] == "GTIN" for a in result["missing_attrs"])


# ── Auto-injeção de SELLER_PACKAGE_* com valores corretos ────────────────

def test_auto_inject_seller_package_values():
    ml_api_attrs = _make_attrs([
        ("SELLER_PACKAGE_HEIGHT", {"hidden": True}),
        ("SELLER_PACKAGE_WIDTH", {"hidden": True}),
        ("SELLER_PACKAGE_LENGTH", {"hidden": True}),
        ("SELLER_PACKAGE_WEIGHT", {"hidden": True}),
    ])
    result = _validate_category_attributes(
        ml_api_attrs=ml_api_attrs,
        baseline=None,
        ui_ml_attributes=[],
        ui_dimensions={"height_cm": 15, "width_cm": 25, "length_cm": 35, "weight_kg": 1.2},
    )
    assert result["status"] == "ok"
    injected = {a["id"]: a for a in result["auto_injected"]}
    assert injected["SELLER_PACKAGE_HEIGHT"]["value_name"] == "15 cm"
    assert injected["SELLER_PACKAGE_HEIGHT"]["value_struct"] == {"number": 15, "unit": "cm"}
    assert injected["SELLER_PACKAGE_WIDTH"]["value_name"] == "25 cm"
    assert injected["SELLER_PACKAGE_LENGTH"]["value_name"] == "35 cm"
    assert injected["SELLER_PACKAGE_WEIGHT"]["value_name"] == "1200 g"
    assert injected["SELLER_PACKAGE_WEIGHT"]["value_struct"] == {"number": 1200, "unit": "g"}


def test_auto_inject_seller_package_enforces_ml_floor():
    """ML rejects seller_package values below 4 cm / 10 g — floor must be applied."""
    ml_api_attrs = _make_attrs([
        ("SELLER_PACKAGE_HEIGHT", {"hidden": True}),
        ("SELLER_PACKAGE_WIDTH", {"hidden": True}),
        ("SELLER_PACKAGE_LENGTH", {"hidden": True}),
        ("SELLER_PACKAGE_WEIGHT", {"hidden": True}),
    ])
    result = _validate_category_attributes(
        ml_api_attrs=ml_api_attrs,
        baseline=None,
        ui_ml_attributes=[],
        ui_dimensions={"height_cm": 2, "width_cm": 1, "length_cm": 3, "weight_kg": 0.005},
    )
    assert result["status"] == "ok"
    injected = {a["id"]: a for a in result["auto_injected"]}
    assert injected["SELLER_PACKAGE_HEIGHT"]["value_struct"]["number"] == 4   # floor: 2 → 4
    assert injected["SELLER_PACKAGE_WIDTH"]["value_struct"]["number"] == 4    # floor: 1 → 4
    assert injected["SELLER_PACKAGE_LENGTH"]["value_struct"]["number"] == 4   # floor: 3 → 4
    assert injected["SELLER_PACKAGE_WEIGHT"]["value_struct"]["number"] == 10  # floor: 5 → 10


# ── Humanization of ML API errors ──────────────────────────────────────────

def test_humanize_gtin_missing_conditional_required():
    raw = ("Validation error | cause: [{'code': "
           "'item.attribute.missing_conditional_required', 'message': 'GTIN is required'}]")
    result = _humanize_ml_error(raw)
    assert "GTIN/EAN" in result
    assert "categoria" in result


def test_humanize_gtin_cross_category():
    raw = ("Validation error | cause: [{'code': "
           "'item.attribute.invalid_product_identifier', 'message': "
           "'Insira um código universal que você não tenha usado'}]")
    result = _humanize_ml_error(raw)
    assert "outra categoria" in result
    assert "GTIN/EAN" in result


def test_humanize_seller_package_dimensions():
    raw = ("Validation error | cause: [{'code': "
           "'item.attribute.invalid.seller.package.dimensions', 'message': "
           "'do not have proper values'}]")
    result = _humanize_ml_error(raw)
    assert "dimensões" in result
    assert "embalagem" in result


def test_humanize_unknown_error_returns_raw():
    raw = "Some unknown ML error"
    assert _humanize_ml_error(raw) == raw
