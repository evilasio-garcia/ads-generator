# tests/test_mercadolivre_freight_comparison.py
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import mercadolivre_service


def test_freight_ok_when_ml_cost_equal_to_adsgen():
    result = mercadolivre_service.compare_freight(
        ml_freight=18.50,
        adsgen_freight=18.50,
    )
    assert result["divergent"] is False
    assert result["ml_freight"] == 18.50
    assert result["adsgen_freight"] == 18.50


def test_freight_ok_when_ml_cost_lower_than_adsgen():
    result = mercadolivre_service.compare_freight(
        ml_freight=15.00,
        adsgen_freight=18.50,
    )
    assert result["divergent"] is False


def test_freight_divergent_when_ml_cost_higher():
    result = mercadolivre_service.compare_freight(
        ml_freight=22.00,
        adsgen_freight=18.50,
    )
    assert result["divergent"] is True
    assert result["ml_freight"] == 22.00
    assert result["adsgen_freight"] == 18.50


def test_recalculate_price_uses_new_freight():
    from pricing.calculators.mercadolivre import MercadoLivrePriceCalculator
    calc = MercadoLivrePriceCalculator()
    ctx = {
        "commission_percent": 0.175,
        "impostos": 0.12,
        "tacos": 0.05,
        "margem_contribuicao": 0.10,
        "lucro": 0.05,
    }
    cost_price = 50.0
    old_freight = 10.0
    new_freight = 22.0

    old_price = calc.get_promo_price(cost_price, old_freight, ctx)
    new_price = mercadolivre_service.recalculate_price_with_new_freight(
        cost_price=cost_price,
        new_freight=new_freight,
        pricing_ctx=ctx,
    )

    assert new_price > old_price
    assert new_price > 0


def test_recalculate_price_returns_positive_when_no_ctx():
    result = mercadolivre_service.recalculate_price_with_new_freight(
        cost_price=50.0,
        new_freight=22.0,
        pricing_ctx=None,
    )
    assert result > 0
