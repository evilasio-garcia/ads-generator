from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from pydantic import BaseModel


class WholesaleTier(BaseModel):
    """Modelo para tier de preço atacado"""
    tier: int
    min_quantity: int
    price: float


class PriceBreakdown(BaseModel):
    """Breakdown detalhado do cálculo de preço"""
    steps: List[Dict[str, Any]]
    notes: Optional[List[str]] = None


class IPriceCalculator(ABC):
    """
    Interface para calculadoras de preço por canal/marketplace.
    
    Todos os métodos recebem cost_price (custo) e opcionalmente ctx (contexto)
    para customizações (categorias, campanhas, políticas específicas).
    """
    
    def __init__(self, channel: str):
        self.channel = channel
    
    @abstractmethod
    def get_listing_price(self, cost_price: float, ctx: Optional[Dict[str, Any]] = None) -> float:
        """
        Calcula o preço de tabela/lista do produto.
        
        Args:
            cost_price: Custo do produto
            ctx: Contexto opcional (categoria, região, política)
            
        Returns:
            Preço de lista (não negativo)
        """
        pass
    
    @abstractmethod
    def get_wholesale_tiers(self, cost_price: float, ctx: Optional[Dict[str, Any]] = None) -> List[WholesaleTier]:
        """
        Calcula faixas de preço para venda atacado.
        
        Args:
            cost_price: Custo do produto
            ctx: Contexto opcional
            
        Returns:
            Lista de tiers ordenada por quantidade crescente, preço decrescente
        """
        pass
    
    @abstractmethod
    def get_aggressive_price(self, cost_price: float, ctx: Optional[Dict[str, Any]] = None) -> float:
        """
        Calcula preço agressivo/competitivo para destacar nos marketplaces.
        
        Args:
            cost_price: Custo do produto
            ctx: Contexto opcional
            
        Returns:
            Preço agressivo (não negativo)
        """
        pass
    
    @abstractmethod
    def get_promo_price(self, cost_price: float, ctx: Optional[Dict[str, Any]] = None) -> float:
        """
        Calcula preço promocional (menor que listing, maior que custo).
        
        Args:
            cost_price: Custo do produto
            ctx: Contexto opcional
            
        Returns:
            Preço promocional (não negativo)
        """
        pass
    
    @abstractmethod
    def get_breakdown(self, cost_price: float, ctx: Optional[Dict[str, Any]] = None) -> PriceBreakdown:
        """
        Retorna breakdown detalhado do cálculo de preços.
        
        Args:
            cost_price: Custo do produto
            ctx: Contexto opcional
            
        Returns:
            PriceBreakdown com steps e notes
        """
        pass
    
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
