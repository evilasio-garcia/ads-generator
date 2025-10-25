from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from pydantic import BaseModel


class PriceMetrics(BaseModel):
    """Métricas financeiras de um preço"""
    margin_percent: float  # % de margem
    value_multiple: float  # múltiplo de valor
    value_amount: float    # valor monetário em R$
    taxes: float           # impostos em R$
    commissions: float     # comissões totais em R$


class PriceWithMetrics(BaseModel):
    """Preço com suas métricas calculadas"""
    price: float
    metrics: PriceMetrics


class WholesaleTier(BaseModel):
    """Modelo para tier de preço atacado"""
    tier: int
    min_quantity: int
    price: float
    metrics: Optional[PriceMetrics] = None


class PriceBreakdown(BaseModel):
    """Breakdown detalhado do cálculo de preço"""
    steps: List[Dict[str, Any]]
    notes: Optional[List[str]] = None


class IPriceCalculator(ABC):
    """
    Interface para calculadoras de preço por canal/marketplace.
    
    Todos os métodos recebem cost_price (custo do produto), shipping_cost (custo de frete)
    e opcionalmente ctx (contexto) para customizações (categorias, campanhas, políticas específicas).
    """
    
    def __init__(self, channel: str):
        self.channel = channel
    
    @abstractmethod
    def get_listing_price(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> float:
        """
        Calcula o preço de tabela/lista do produto.
        
        Args:
            cost_price: Custo do produto
            shipping_cost: Custo de frete/envio
            ctx: Contexto opcional (categoria, região, política)
            
        Returns:
            Preço de lista (não negativo)
        """
        pass
    
    @abstractmethod
    def get_wholesale_tiers(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> List[WholesaleTier]:
        """
        Calcula faixas de preço para venda atacado.
        
        Args:
            cost_price: Custo do produto
            shipping_cost: Custo de frete/envio
            ctx: Contexto opcional
            
        Returns:
            Lista de tiers ordenada por quantidade crescente, preço decrescente
        """
        pass
    
    @abstractmethod
    def get_aggressive_price(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> float:
        """
        Calcula preço agressivo/competitivo para destacar nos marketplaces.
        
        Args:
            cost_price: Custo do produto
            shipping_cost: Custo de frete/envio
            ctx: Contexto opcional
            
        Returns:
            Preço agressivo (não negativo)
        """
        pass
    
    @abstractmethod
    def get_promo_price(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> float:
        """
        Calcula preço promocional (menor que listing, maior que custo).
        
        Args:
            cost_price: Custo do produto
            shipping_cost: Custo de frete/envio
            ctx: Contexto opcional
            
        Returns:
            Preço promocional (não negativo)
        """
        pass
    
    @abstractmethod
    def get_breakdown(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> PriceBreakdown:
        """
        Retorna breakdown detalhado do cálculo de preços.
        
        Args:
            cost_price: Custo do produto
            shipping_cost: Custo de frete/envio
            ctx: Contexto opcional
            
        Returns:
            PriceBreakdown com steps e notes
        """
        pass
    
    def calculate_metrics(self, price: float, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> PriceMetrics:
        """
        Calcula métricas financeiras para um preço.
        
        Args:
            price: Preço do produto
            cost_price: Custo do produto
            shipping_cost: Custo de frete/envio
            ctx: Contexto com taxas (impostos, comissões, etc.)
            
        Returns:
            PriceMetrics com todas as métricas calculadas
        """
        total_cost = cost_price + shipping_cost
        
        # Obter taxas do contexto ou usar defaults
        if ctx is None:
            ctx = {}
        
        impostos_pct = ctx.get('impostos', 0.0)
        commission_pct = ctx.get('commission_percent', 0.0)
        tacos_pct = ctx.get('tacos', 0.0)
        margem_contrib_pct = ctx.get('margem_contribuicao', 0.0)
        lucro_pct = ctx.get('lucro', 0.0)
        
        # Calcular impostos e comissões
        taxes = price * impostos_pct
        commissions = price * (commission_pct + tacos_pct + margem_contrib_pct + lucro_pct)
        
        # Calcular valor monetário
        value_amount = price - total_cost - taxes - commissions
        
        # Calcular % de margem
        margin_percent = (value_amount / price * 100) if price > 0 else 0.0
        
        # Calcular múltiplo de valor
        value_multiple = (value_amount / total_cost) if total_cost > 0 else 0.0
        
        return PriceMetrics(
            margin_percent=round(margin_percent, 2),
            value_multiple=round(value_multiple, 2),
            value_amount=round(value_amount, 2),
            taxes=round(taxes, 2),
            commissions=round(commissions, 2)
        )
    
    def get_listing_price_with_metrics(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> PriceWithMetrics:
        """Retorna preço de lista com métricas"""
        price = self.get_listing_price(cost_price, shipping_cost, ctx)
        metrics = self.calculate_metrics(price, cost_price, shipping_cost, ctx)
        return PriceWithMetrics(price=price, metrics=metrics)
    
    def get_aggressive_price_with_metrics(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> PriceWithMetrics:
        """Retorna preço agressivo com métricas"""
        price = self.get_aggressive_price(cost_price, shipping_cost, ctx)
        metrics = self.calculate_metrics(price, cost_price, shipping_cost, ctx)
        return PriceWithMetrics(price=price, metrics=metrics)
    
    def get_promo_price_with_metrics(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> PriceWithMetrics:
        """Retorna preço promocional com métricas"""
        price = self.get_promo_price(cost_price, shipping_cost, ctx)
        metrics = self.calculate_metrics(price, cost_price, shipping_cost, ctx)
        return PriceWithMetrics(price=price, metrics=metrics)
    
    def get_wholesale_tiers_with_metrics(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> List[WholesaleTier]:
        """Retorna tiers de atacado com métricas"""
        tiers = self.get_wholesale_tiers(cost_price, shipping_cost, ctx)
        # Adicionar métricas para cada tier
        for tier in tiers:
            tier.metrics = self.calculate_metrics(tier.price, cost_price, shipping_cost, ctx)
        return tiers
    
    def apply_rounding(self, price: float, ctx: Optional[Dict[str, Any]] = None) -> float:
        """
        Aplica arredondamento configurável (ex: final .99).
        
        Args:
            price: Preço calculado
            ctx: Contexto com preferências de arredondamento
            
        Returns:
            Preço arredondado
        """
        if ctx and ctx.get('rounding') == 'none':
            return round(price, 2)
        
        # Default: arredonda para .99
        if price < 1.0:
            return round(price, 2)
        
        return float(int(price)) + 0.99
    
    def ensure_non_negative(self, price: float) -> float:
        """Garante que o preço nunca seja negativo"""
        return max(0.0, price)
