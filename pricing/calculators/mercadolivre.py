from typing import Dict, Any, Optional, List
from .base import BasePriceCalculator
from pricing.interface import WholesaleTier, PriceBreakdown


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
    
    def get_listing_price(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> float:
        """
        Calcula o preço de tabela/lista para Mercado Livre.
        
        ADICIONE SUA LÓGICA CUSTOMIZADA AQUI.
        Por exemplo:
        - Ajustes baseados em categoria do produto
        - Preços dinâmicos baseados em concorrência
        - Margens diferenciadas por região
        """
        # Comportamento padrão (pode sobrescrever completamente)
        return super().get_listing_price(cost_price, shipping_cost, ctx)
    
    def get_wholesale_tiers(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> List[WholesaleTier]:
        """
        Calcula faixas de preço para atacado no Mercado Livre.
        
        ADICIONE SUA LÓGICA CUSTOMIZADA AQUI.
        Por exemplo:
        - Tiers personalizados (3, 6, 12+ unidades)
        - Descontos progressivos mais agressivos
        - Faixas específicas por categoria
        """
        # Comportamento padrão (pode sobrescrever completamente)
        return super().get_wholesale_tiers(cost_price, shipping_cost, ctx)
    
    def get_aggressive_price(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> float:
        """
        Calcula preço agressivo/competitivo para Mercado Livre.
        
        ADICIONE SUA LÓGICA CUSTOMIZADA AQUI.
        Por exemplo:
        - Desconto maior em categorias competitivas
        - Ajuste baseado em reputação do vendedor
        - Preço dinâmico baseado em estoque
        """
        # Comportamento padrão (pode sobrescrever completamente)
        return super().get_aggressive_price(cost_price, shipping_cost, ctx)
    
    def get_promo_price(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> float:
        """
        Calcula preço promocional para Mercado Livre.
        
        ADICIONE SUA LÓGICA CUSTOMIZADA AQUI.
        Por exemplo:
        - Preços especiais para eventos (Black Friday, etc)
        - Descontos maiores para produtos parados
        - Promoções relâmpago
        """
        # Comportamento padrão (pode sobrescrever completamente)
        return super().get_promo_price(cost_price, shipping_cost, ctx)
    
    def get_breakdown(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> PriceBreakdown:
        """
        Retorna breakdown detalhado do cálculo de preços para Mercado Livre.
        
        ADICIONE SUA LÓGICA CUSTOMIZADA AQUI.
        Por exemplo:
        - Adicionar steps extras (custo de embalagem, etc)
        - Incluir notas específicas do Mercado Livre
        - Detalhar comissões e taxas adicionais
        """
        # Comportamento padrão (pode sobrescrever completamente)
        return super().get_breakdown(cost_price, shipping_cost, ctx)
