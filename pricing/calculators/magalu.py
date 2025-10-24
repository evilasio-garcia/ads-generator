from .base import BasePriceCalculator


class MagaluPriceCalculator(BasePriceCalculator):
    """
    Calculadora de preços para Magazine Luiza.
    
    Características:
    - Marketplace nacional consolidado
    - Comissão intermediária
    - Concorrência moderada
    """
    
    DEFAULT_MARKUP = 2.0  # 100% de markup
    DEFAULT_TAX_RATE = 0.14  # 14% comissão Magalu
    MIN_MARGIN = 0.22  # 22% margem mínima
    AGGRESSIVE_DISCOUNT = 0.10  # 10% desconto agressivo
    PROMO_DISCOUNT = 0.15  # 15% desconto promocional
    
    def __init__(self):
        super().__init__(channel="magalu")
