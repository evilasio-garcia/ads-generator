import pytest
from pricing import PriceCalculatorFactory


def test_listing_price_is_greater_than_cost():
    """Testa se preço de lista é sempre maior que custo"""
    calc = PriceCalculatorFactory.get("mercadolivre")
    cost = 100.0
    
    listing_price = calc.get_listing_price(cost)
    
    assert listing_price > cost


def test_prices_are_non_negative():
    """Testa se todos os preços são não-negativos"""
    calc = PriceCalculatorFactory.get("shopee")
    cost = 50.0
    
    assert calc.get_listing_price(cost) >= 0
    assert calc.get_aggressive_price(cost) >= 0
    assert calc.get_promo_price(cost) >= 0


def test_aggressive_price_is_less_than_listing():
    """Testa se preço agressivo é menor que preço de lista"""
    calc = PriceCalculatorFactory.get("amazon")
    cost = 100.0
    
    listing = calc.get_listing_price(cost)
    aggressive = calc.get_aggressive_price(cost)
    
    assert aggressive < listing


def test_promo_price_is_less_than_listing():
    """Testa se preço promocional é menor que preço de lista"""
    calc = PriceCalculatorFactory.get("magalu")
    cost = 100.0
    
    listing = calc.get_listing_price(cost)
    promo = calc.get_promo_price(cost)
    
    assert promo < listing


def test_wholesale_tiers_are_monotonic():
    """Testa se tiers de atacado têm preços decrescentes"""
    calc = PriceCalculatorFactory.get("ecommerce")
    cost = 100.0
    
    tiers = calc.get_wholesale_tiers(cost)
    
    assert len(tiers) > 0
    
    # Verificar que preços diminuem com quantidade
    for i in range(len(tiers) - 1):
        assert tiers[i].price >= tiers[i + 1].price
        assert tiers[i].min_quantity < tiers[i + 1].min_quantity


def test_different_channels_produce_different_prices():
    """Testa se canais diferentes produzem preços diferentes para mesmo custo"""
    cost = 100.0
    
    ml_calc = PriceCalculatorFactory.get("mercadolivre")
    shopee_calc = PriceCalculatorFactory.get("shopee")
    
    ml_price = ml_calc.get_listing_price(cost)
    shopee_price = shopee_calc.get_listing_price(cost)
    
    # Preços devem ser diferentes devido a markups/taxas distintas
    assert ml_price != shopee_price


def test_rounding_applies_99_cents():
    """Testa se arredondamento aplica .99 por padrão"""
    calc = PriceCalculatorFactory.get("telemarketing")
    cost = 100.0
    
    listing = calc.get_listing_price(cost)
    
    # Verificar que termina em .99
    assert str(listing).endswith(".99")


def test_breakdown_contains_all_steps():
    """Testa se breakdown contém todos os passos esperados"""
    calc = PriceCalculatorFactory.get("shein")
    cost = 100.0
    
    breakdown = calc.get_breakdown(cost)
    
    assert len(breakdown.steps) >= 4
    assert breakdown.notes is not None
    assert len(breakdown.notes) >= 1


def test_custom_context_changes_pricing():
    """Testa se contexto customizado altera precificação"""
    calc = PriceCalculatorFactory.get("mercadolivre")
    cost = 100.0
    
    default_price = calc.get_listing_price(cost)
    custom_price = calc.get_listing_price(cost, ctx={"markup": 3.0})
    
    # Markup maior deve produzir preço maior
    assert custom_price > default_price
