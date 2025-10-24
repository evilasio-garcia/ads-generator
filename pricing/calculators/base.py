from typing import Dict, Any, Optional, List
from pricing.interface import IPriceCalculator, WholesaleTier, PriceBreakdown


class BasePriceCalculator(IPriceCalculator):
    """
    Classe base com lógica comum para todas as calculadoras.
    Evita duplicação de código entre implementações.
    """
    
    # Configurações padrão (podem ser sobrescritas)
    DEFAULT_MARKUP = 2.0  # 100% de markup
    DEFAULT_TAX_RATE = 0.15  # 15% de impostos
    MIN_MARGIN = 0.20  # 20% de margem mínima
    AGGRESSIVE_DISCOUNT = 0.10  # 10% de desconto no preço agressivo
    PROMO_DISCOUNT = 0.15  # 15% de desconto promocional
    
    def calculate_base_price(self, cost_price: float, markup: float, tax_rate: float) -> float:
        """Calcula preço base com markup e impostos"""
        return cost_price * (1 + markup) / (1 - tax_rate)
    
    def get_listing_price(self, cost_price: float, ctx: Optional[Dict[str, Any]] = None) -> float:
        markup = ctx.get('markup', self.DEFAULT_MARKUP) if ctx else self.DEFAULT_MARKUP
        tax_rate = ctx.get('tax_rate', self.DEFAULT_TAX_RATE) if ctx else self.DEFAULT_TAX_RATE
        
        base_price = self.calculate_base_price(cost_price, markup, tax_rate)
        rounded_price = self.apply_rounding(base_price, ctx)
        
        return self.ensure_non_negative(rounded_price)
    
    def get_wholesale_tiers(self, cost_price: float, ctx: Optional[Dict[str, Any]] = None) -> List[WholesaleTier]:
        listing_price = self.get_listing_price(cost_price, ctx)
        
        # Tiers padrão: 5-10-20 unidades com descontos crescentes
        tiers = [
            WholesaleTier(tier=1, min_quantity=5, price=self.apply_rounding(listing_price * 0.95, ctx)),
            WholesaleTier(tier=2, min_quantity=10, price=self.apply_rounding(listing_price * 0.90, ctx)),
            WholesaleTier(tier=3, min_quantity=20, price=self.apply_rounding(listing_price * 0.85, ctx)),
        ]
        
        return tiers
    
    def get_aggressive_price(self, cost_price: float, ctx: Optional[Dict[str, Any]] = None) -> float:
        listing_price = self.get_listing_price(cost_price, ctx)
        discount = ctx.get('aggressive_discount', self.AGGRESSIVE_DISCOUNT) if ctx else self.AGGRESSIVE_DISCOUNT
        
        aggressive = listing_price * (1 - discount)
        rounded = self.apply_rounding(aggressive, ctx)
        
        return self.ensure_non_negative(rounded)
    
    def get_promo_price(self, cost_price: float, ctx: Optional[Dict[str, Any]] = None) -> float:
        listing_price = self.get_listing_price(cost_price, ctx)
        discount = ctx.get('promo_discount', self.PROMO_DISCOUNT) if ctx else self.PROMO_DISCOUNT
        
        promo = listing_price * (1 - discount)
        rounded = self.apply_rounding(promo, ctx)
        
        return self.ensure_non_negative(rounded)
    
    def get_breakdown(self, cost_price: float, ctx: Optional[Dict[str, Any]] = None) -> PriceBreakdown:
        markup = ctx.get('markup', self.DEFAULT_MARKUP) if ctx else self.DEFAULT_MARKUP
        tax_rate = ctx.get('tax_rate', self.DEFAULT_TAX_RATE) if ctx else self.DEFAULT_TAX_RATE
        
        base_price = cost_price * (1 + markup)
        final_price = base_price / (1 - tax_rate)
        
        steps = [
            {"label": "Custo do produto", "value": cost_price},
            {"label": f"Markup ({markup*100:.0f}%)", "value": base_price},
            {"label": f"Impostos ({tax_rate*100:.0f}%)", "value": final_price},
            {"label": "Preço de lista (arredondado)", "value": self.get_listing_price(cost_price, ctx)},
            {"label": "Preço agressivo", "value": self.get_aggressive_price(cost_price, ctx)},
            {"label": "Preço promocional", "value": self.get_promo_price(cost_price, ctx)},
        ]
        
        notes = [
            f"Canal: {self.channel}",
            f"Margem mínima configurada: {self.MIN_MARGIN*100:.0f}%"
        ]
        
        return PriceBreakdown(steps=steps, notes=notes)
