import pytest
from pricing import PriceCalculatorFactory
from pricing.interface import IPriceCalculator


def test_factory_returns_correct_calculator_for_mercadolivre():
    """Testa se Factory retorna calculadora correta para Mercado Livre"""
    calc = PriceCalculatorFactory.get("mercadolivre")
    assert calc is not None
    assert isinstance(calc, IPriceCalculator)
    assert calc.channel == "mercadolivre"


def test_factory_returns_correct_calculator_for_all_channels():
    """Testa se Factory retorna calculadoras para todos os canais suportados"""
    channels = ["mercadolivre", "shopee", "amazon", "shein", "magalu", "ecommerce", "telemarketing"]
    
    for channel in channels:
        calc = PriceCalculatorFactory.get(channel)
        assert calc is not None
        assert isinstance(calc, IPriceCalculator)
        assert calc.channel == channel


def test_factory_raises_error_for_unsupported_channel():
    """Testa se Factory levanta erro para canal não suportado"""
    with pytest.raises(ValueError) as exc_info:
        PriceCalculatorFactory.get("canal_inexistente")
    
    assert "não suportado" in str(exc_info.value)


def test_factory_is_case_insensitive():
    """Testa se Factory é case-insensitive"""
    calc1 = PriceCalculatorFactory.get("MERCADOLIVRE")
    calc2 = PriceCalculatorFactory.get("MercadoLivre")
    calc3 = PriceCalculatorFactory.get("mercadolivre")
    
    assert calc1.channel == calc2.channel == calc3.channel == "mercadolivre"


def test_factory_get_supported_channels():
    """Testa se get_supported_channels retorna lista correta"""
    channels = PriceCalculatorFactory.get_supported_channels()
    
    assert isinstance(channels, list)
    assert len(channels) == 7
    assert "mercadolivre" in channels
    assert "shopee" in channels


def test_factory_is_supported():
    """Testa método is_supported"""
    assert PriceCalculatorFactory.is_supported("mercadolivre") is True
    assert PriceCalculatorFactory.is_supported("SHOPEE") is True
    assert PriceCalculatorFactory.is_supported("canal_inexistente") is False
