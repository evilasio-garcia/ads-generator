from .base import BasePriceCalculator


class AmazonBRPriceCalculator(BasePriceCalculator):
    """
    Calculadora de preços para Amazon BR.
    
    Características:
    - Taxas elevadas (comissão + FBA)
    - Público aceita preços mais altos
    - Logística Amazon (FBA) adiciona custo
    """
    
    DEFAULT_MARKUP = 2.5  # 150% de markup (compensar FBA)
    DEFAULT_TAX_RATE = 0.18  # 18% comissão + FBA
    MIN_MARGIN = 0.30  # 30% margem mínima
    AGGRESSIVE_DISCOUNT = 0.08  # 8% desconto agressivo
    PROMO_DISCOUNT = 0.12  # 12% desconto promocional
    
    def __init__(self):
        super().__init__(channel="amazon")
