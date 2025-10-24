from .base import BasePriceCalculator


class EcommercePriceCalculator(BasePriceCalculator):
    """
    Calculadora de preços para E-commerce próprio.
    
    Características:
    - Sem comissão de marketplace
    - Margem mais alta possível
    - Controle total de pricing
    """
    
    DEFAULT_MARKUP = 2.8  # 180% de markup (sem comissão)
    DEFAULT_TAX_RATE = 0.05  # 5% custos operacionais
    MIN_MARGIN = 0.35  # 35% margem mínima
    AGGRESSIVE_DISCOUNT = 0.10  # 10% desconto agressivo
    PROMO_DISCOUNT = 0.20  # 20% desconto promocional
    
    def __init__(self):
        super().__init__(channel="ecommerce")
