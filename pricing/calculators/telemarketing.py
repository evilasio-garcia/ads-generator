from .base import BasePriceCalculator


class TelemarketingPriceCalculator(BasePriceCalculator):
    """
    Calculadora de preços para canal Telemarketing.
    
    Características:
    - Venda direta com operador
    - Margem alta (custo de equipe)
    - Possibilidade de negociação
    """
    
    DEFAULT_MARKUP = 3.0  # 200% de markup (compensar equipe)
    DEFAULT_TAX_RATE = 0.08  # 8% custos operacionais
    MIN_MARGIN = 0.40  # 40% margem mínima
    AGGRESSIVE_DISCOUNT = 0.15  # 15% desconto agressivo (negociação)
    PROMO_DISCOUNT = 0.25  # 25% desconto promocional (campanha)
    
    def __init__(self):
        super().__init__(channel="telemarketing")
