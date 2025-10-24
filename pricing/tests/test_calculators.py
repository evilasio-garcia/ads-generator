import pytest
from pricing import PriceCalculatorFactory


def test_listing_price_is_greater_than_cost():
    """Testa se preço de lista é sempre maior que custo total"""
    calc = PriceCalculatorFactory.get("mercadolivre")
    cost = 100.0
    shipping = 10.0
    
    listing_price = calc.get_listing_price(cost, shipping)
    
    assert listing_price > (cost + shipping)


def test_prices_are_non_negative():
    """Testa se todos os preços são não-negativos"""
    calc = PriceCalculatorFactory.get("shopee")
    cost = 50.0
    shipping = 5.0
    
    assert calc.get_listing_price(cost, shipping) >= 0
    assert calc.get_aggressive_price(cost, shipping) >= 0
    assert calc.get_promo_price(cost, shipping) >= 0


def test_aggressive_price_is_less_than_listing():
    """Testa se preço agressivo é menor que preço de lista"""
    calc = PriceCalculatorFactory.get("amazon")
    cost = 100.0
    shipping = 15.0
    
    listing = calc.get_listing_price(cost, shipping)
    aggressive = calc.get_aggressive_price(cost, shipping)
    
    assert aggressive < listing


def test_promo_price_is_less_than_listing():
    """Testa se preço promocional é menor que preço de lista"""
    calc = PriceCalculatorFactory.get("magalu")
    cost = 100.0
    shipping = 10.0
    
    listing = calc.get_listing_price(cost, shipping)
    promo = calc.get_promo_price(cost, shipping)
    
    assert promo < listing


def test_wholesale_tiers_are_monotonic():
    """Testa se tiers de atacado têm preços decrescentes"""
    calc = PriceCalculatorFactory.get("ecommerce")
    cost = 100.0
    shipping = 12.0
    
    tiers = calc.get_wholesale_tiers(cost, shipping)
    
    assert len(tiers) > 0
    
    # Verificar que preços diminuem com quantidade
    for i in range(len(tiers) - 1):
        assert tiers[i].price >= tiers[i + 1].price
        assert tiers[i].min_quantity < tiers[i + 1].min_quantity


def test_different_channels_produce_different_prices():
    """Testa se canais diferentes produzem preços diferentes para mesmo custo"""
    cost = 100.0
    shipping = 8.0
    
    ml_calc = PriceCalculatorFactory.get("mercadolivre")
    shopee_calc = PriceCalculatorFactory.get("shopee")
    
    ml_price = ml_calc.get_listing_price(cost, shipping)
    shopee_price = shopee_calc.get_listing_price(cost, shipping)
    
    # Preços devem ser diferentes devido a markups/taxas distintas
    assert ml_price != shopee_price


def test_rounding_applies_99_cents():
    """Testa se arredondamento aplica .99 por padrão"""
    calc = PriceCalculatorFactory.get("telemarketing")
    cost = 100.0
    shipping = 5.0
    
    listing = calc.get_listing_price(cost, shipping)
    
    # Verificar que termina em .99
    assert str(listing).endswith(".99")


def test_breakdown_contains_all_steps():
    """Testa se breakdown contém todos os passos esperados"""
    calc = PriceCalculatorFactory.get("shein")
    cost = 100.0
    shipping = 7.0
    
    breakdown = calc.get_breakdown(cost, shipping)
    
    # Agora temos 3 passos extras: custo produto, custo frete, custo total
    assert len(breakdown.steps) >= 7
    assert breakdown.notes is not None
    assert len(breakdown.notes) >= 1


def test_custom_context_changes_pricing():
    """Testa se contexto customizado altera precificação"""
    calc = PriceCalculatorFactory.get("mercadolivre")
    cost = 100.0
    shipping = 10.0
    
    default_price = calc.get_listing_price(cost, shipping)
    custom_price = calc.get_listing_price(cost, shipping, ctx={"markup": 3.0})
    
    # Markup maior deve produzir preço maior
    assert custom_price > default_price


def test_shipping_cost_increases_final_price():
    """Testa se custo de frete aumenta o preço final"""
    calc = PriceCalculatorFactory.get("amazon")
    cost = 100.0
    
    price_without_shipping = calc.get_listing_price(cost, 0.0)
    price_with_shipping = calc.get_listing_price(cost, 20.0)
    
    # Preço com frete deve ser maior
    assert price_with_shipping > price_without_shipping
