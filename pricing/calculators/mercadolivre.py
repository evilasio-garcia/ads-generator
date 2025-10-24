from .base import BasePriceCalculator


class MercadoLivrePriceCalculator(BasePriceCalculator):
    """
    Calculadora de preços para Mercado Livre.
    
    Características:
    - Taxa de comissão: ~15%
    - Frete influencia competitividade
    - Markup padrão alto devido à concorrência
    """
    
    DEFAULT_MARKUP = 2.2  # 120% de markup (competitivo no ML)
    DEFAULT_TAX_RATE = 0.15  # 15% de comissão ML
    MIN_MARGIN = 0.25  # 25% margem mínima
    AGGRESSIVE_DISCOUNT = 0.12  # 12% desconto agressivo
    PROMO_DISCOUNT = 0.18  # 18% desconto promocional
    
    def __init__(self):
        super().__init__(channel="mercadolivre")
