from .base import BasePriceCalculator


class SheinPriceCalculator(BasePriceCalculator):
    """
    Calculadora de preços para Shein.
    
    Características:
    - Foco em moda/fashion
    - Preços muito competitivos
    - Alto volume, margem baixa
    """
    
    DEFAULT_MARKUP = 1.6  # 60% de markup (ultra competitivo)
    DEFAULT_TAX_RATE = 0.10  # 10% comissão
    MIN_MARGIN = 0.15  # 15% margem mínima
    AGGRESSIVE_DISCOUNT = 0.18  # 18% desconto agressivo
    PROMO_DISCOUNT = 0.25  # 25% desconto promocional
    
    def __init__(self):
        super().__init__(channel="shein")
