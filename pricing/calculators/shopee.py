from .base import BasePriceCalculator


class ShopeePriceCalculator(BasePriceCalculator):
    """
    Calculadora de preços para Shopee.
    
    Características:
    - Público busca preço baixo
    - Comissão variável por categoria
    - Foco em volume
    """
    
    DEFAULT_MARKUP = 1.8  # 80% de markup (mais agressivo)
    DEFAULT_TAX_RATE = 0.12  # 12% comissão Shopee
    MIN_MARGIN = 0.20  # 20% margem mínima
    AGGRESSIVE_DISCOUNT = 0.15  # 15% desconto agressivo
    PROMO_DISCOUNT = 0.20  # 20% desconto promocional
    
    def __init__(self):
        super().__init__(channel="shopee")
