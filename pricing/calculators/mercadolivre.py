from typing import Dict, Any, Optional, List

from pricing.interface import WholesaleTier, PriceBreakdown
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

    def calc_fixed_commission_tax(self, price: float):
        if price < 29:
            return 6.25
        elif price < 50:
            return 6.5
        elif price < 79:
            return 6.75
        return 0.0

    def calculate_total_cost(self, cost_price: float, shipping_cost: float) -> float:
        estimated_list_price = cost_price * 1.7 if cost_price < 79 else cost_price
        fixed_tax = self.calc_fixed_commission_tax(estimated_list_price)

        """Calcula custo total (produto + frete + taxa fixa por venda)"""
        return cost_price + shipping_cost + fixed_tax

    def __init__(self):
        super().__init__(channel="mercadolivre")

    def get_listing_price(self, cost_price: float, shipping_cost: float = 0.0,
                          ctx: Optional[Dict[str, Any]] = None) -> float:
        """
        Calcula o preço de tabela/lista para Mercado Livre baseado em:
        """
        promo_price = self.get_promo_price(cost_price, shipping_cost, ctx)

        return self.roundup(promo_price / (1 - 0.15), 2)  # Acresce 15% no preço promocional

    def get_wholesale_tiers(self, cost_price: float, shipping_cost: float = 0.0,
                            ctx: Optional[Dict[str, Any]] = None) -> List[WholesaleTier]:
        """
        Calcula faixas de preço para atacado no Mercado Livre.
        """
        if not ctx:
            return super().get_wholesale_tiers(cost_price, shipping_cost, ctx)

        lucro = float(ctx.get('lucro', 0.05))  # Lucro: padrão 5%
        tacos = float(ctx.get('tacos', 0.05))  # Investimento em publicidade: padrão 5%
        margem_contribuicao = float(ctx.get('margem_contribuicao', 0.10))  # M.C.: padrão 10%

        ctx['lucro'] = lucro / 3 if lucro > 0 else 0.0
        ctx['tacos'] = tacos / 3 if tacos > 0 else 0.0
        ctx['margem_contribuicao'] = margem_contribuicao / 3 if margem_contribuicao > 0 else 0.0
        wholesale_tier1_price = self.get_promo_price(cost_price, shipping_cost, ctx)
        wholesale_tier1_quant = self.get_promo_price_with_metrics(cost_price, shipping_cost, ctx).metrics.value_multiple

        ctx['lucro'] = lucro / 5 if lucro > 0 else 0.0
        ctx['tacos'] = tacos / 5 if tacos > 0 else 0.0
        ctx['margem_contribuicao'] = margem_contribuicao / 5 if margem_contribuicao > 0 else 0.0
        wholesale_tier2_price = self.get_promo_price(cost_price, shipping_cost, ctx)
        wholesale_tier2_quant = self.get_promo_price_with_metrics(cost_price, shipping_cost, ctx).metrics.value_multiple

        ctx['lucro'] = 0.0
        ctx['tacos'] = tacos / 10 if tacos > 0 else 0.0
        ctx['margem_contribuicao'] = margem_contribuicao / 10 if margem_contribuicao > 0 else 0.0
        wholesale_tier3_price = self.get_promo_price(cost_price, shipping_cost, ctx)
        wholesale_tier3_quant = self.get_promo_price_with_metrics(cost_price, shipping_cost, ctx).metrics.value_multiple

        tiers = [
            WholesaleTier(tier=1, min_quantity=self.roundup(wholesale_tier1_quant, 0),
                          price=self.roundup(wholesale_tier1_price, 2)),
            WholesaleTier(tier=2, min_quantity=self.roundup(wholesale_tier2_quant, 0),
                          price=self.roundup(wholesale_tier2_price, 2)),
            WholesaleTier(tier=3, min_quantity=self.roundup(wholesale_tier3_quant, 0),
                          price=self.roundup(wholesale_tier3_price, 2)),
        ]

        ctx['lucro'] = lucro
        ctx['tacos'] = tacos
        ctx['margem_contribuicao'] = margem_contribuicao

        return tiers

    def get_aggressive_price(self, cost_price: float, shipping_cost: float = 0.0,
                             ctx: Optional[Dict[str, Any]] = None) -> float:
        """
        Calcula preço agressivo/competitivo para Mercado Livre.
        """
        if not ctx:
            return super().get_listing_price(cost_price, shipping_cost, ctx)

        margem_contribuicao = float(ctx.get('margem_contribuicao', 0.10))
        margem_contribuicao_agressiva = margem_contribuicao / 3 if margem_contribuicao > 0 else 0.0
        ctx['margem_contribuicao'] = margem_contribuicao_agressiva

        aggressive_price = self.get_promo_price(cost_price, shipping_cost, ctx)

        ctx['margem_contribuicao'] = margem_contribuicao

        return aggressive_price

    def get_promo_price(self, cost_price: float, shipping_cost: float = 0.0,
                        ctx: Optional[Dict[str, Any]] = None) -> float:
        """
        Calcula preço promocional para Mercado Livre.
        """
        if not ctx:
            return super().get_listing_price(cost_price, shipping_cost, ctx)

        # Obter dados de precificação do contexto
        taxa_comissao = float(ctx.get('commission_percent', 0.175))  # Considera que é Premium por padrão
        impostos = float(ctx.get('impostos', 0.12))  # Padrão 12%
        tacos = float(ctx.get('tacos', 0.05))  # Investimento em publicidade: padrão 5%
        margem_contribuicao = float(ctx.get('margem_contribuicao', 0.10))  # M.C.: padrão 10%
        lucro = float(ctx.get('lucro', 0.05))  # Lucro: padrão 5%

        # Calcular custo total (produto + frete)
        custo_total = self.calculate_total_cost(cost_price, shipping_cost)

        # Calcular denominador (1 - soma de todos os percentuais)
        # Preço = Custo / (1 - %taxa_comissao - %impostos - %tacos - %mc - %lucro)
        soma_percentuais = taxa_comissao + impostos + tacos + margem_contribuicao + lucro

        denominador = 1 - soma_percentuais

        # Calcular preço base
        preco_base = custo_total / denominador

        return self.roundup(preco_base, 2)

    def get_breakdown(self, cost_price: float, shipping_cost: float = 0.0,
                      ctx: Optional[Dict[str, Any]] = None) -> PriceBreakdown:
        """
        Retorna breakdown detalhado do cálculo de preços para Mercado Livre.
        Mostra todos os componentes de custo e margem.
        """
        if not ctx:
            return super().get_breakdown(cost_price, shipping_cost, ctx)

        # Usar comissão diretamente informada
        comissao = float(ctx.get('commission_percent', 0.15))
        tipo_anuncio = f"{comissao * 100:.1f}%"  # Exibir percentual no breakdown

        # Obter configurações
        impostos = ctx.get('impostos', 0.08)
        tacos = ctx.get('tacos', 0.05)
        margem_contribuicao = ctx.get('margem_contribuicao', 0.15)
        lucro = ctx.get('lucro', 0.10)
        custo_total = self.calculate_total_cost(cost_price, shipping_cost)
        preco_final = self.get_listing_price(cost_price, shipping_cost, ctx)
        fixed_commission_tax_cost = custo_total - cost_price - shipping_cost

        # Calcular valores absolutos
        valor_comissao = preco_final * comissao
        valor_impostos = preco_final * impostos
        valor_tacos = preco_final * tacos
        valor_mc = preco_final * margem_contribuicao
        valor_lucro = preco_final * lucro

        steps = [
            {"label": "💰 Custo do produto", "value": cost_price},
            {"label": "📦 Custo de frete", "value": shipping_cost},
            {"label": "🏪 Taxa fixa de comissão", "value": fixed_commission_tax_cost},
            {"label": "➕ Custo total (produto + frete)", "value": custo_total},
            {"label": "─────────────────────", "value": 0},
            {"label": f"🏪 Comissão ML {tipo_anuncio.title()}", "value": valor_comissao},
            {"label": f"🧾 Impostos ({impostos * 100:.1f}%)", "value": valor_impostos},
            {"label": f"📢 Investimento Publicidade/TACOS ({tacos * 100:.1f}%)", "value": valor_tacos},
            {"label": f"📊 Margem de Contribuição ({margem_contribuicao * 100:.1f}%)", "value": valor_mc},
            {"label": f"💵 Lucro ({lucro * 100:.1f}%)", "value": valor_lucro},
            {"label": "─────────────────────", "value": 0},
            {"label": "🏷️ PREÇO FINAL", "value": preco_final},
        ]

        notes = [
            f"Canal: Mercado Livre ({tipo_anuncio.title()})",
            f"Soma de percentuais: {(comissao + impostos + tacos + margem_contribuicao + lucro) * 100:.1f}%",
            f"Markup aplicado: {((preco_final / custo_total - 1) * 100):.1f}%",
            "Clássico ML: 10-14% comissão | Premium ML: 15-19% comissão"
        ]

        return PriceBreakdown(steps=steps, notes=notes)
