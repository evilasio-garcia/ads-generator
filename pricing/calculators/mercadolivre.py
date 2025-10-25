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

    def calculate_total_cost(self, cost_price: float, shipping_cost: float) -> float:
        fixed_tax = 0.0
        estimated_list_price = cost_price * 1.7 if cost_price < 79 else cost_price

        if estimated_list_price < 29:
            fixed_tax = 6.25
        elif estimated_list_price < 50:
            fixed_tax = 6.5
        elif estimated_list_price < 79:
            fixed_tax = 6.75

        """Calcula custo total (produto + frete)"""
        return cost_price + shipping_cost + fixed_tax

    def __init__(self):
        super().__init__(channel="mercadolivre")
    
    def get_listing_price(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> float:
        """
        Calcula o preço de tabela/lista para Mercado Livre baseado em:
        - Comissão ML (Clássico ou Premium)
        - Impostos
        - Investimento em publicidade (% TACOS)
        - Margem de contribuição (M.C.)
        - Lucro desejado
        
        Fórmula:
        Preço = (Custo Total + Frete) / (1 - %Comissão - %Impostos - %TACOS - %MC - %Lucro)
        """
        if not ctx:
            # Fallback para comportamento padrão se não houver contexto
            return super().get_listing_price(cost_price, shipping_cost, ctx)
        
        # Obter percentual de comissão: priorizar commission_percent se fornecido
        if 'commission_percent' in ctx and ctx.get('commission_percent') is not None:
            # Usar comissão diretamente informada
            comissao = float(ctx.get('commission_percent', 0.15))
        else:
            # Fallback: usar lógica antiga (min/max)
            comissao_min = ctx.get('comissao_min', 0.125)  # Clássico: padrão 12.5%
            comissao_max = ctx.get('comissao_max', 0.175)  # Premium: padrão 17.5%
            tipo_anuncio = ctx.get('tipo_anuncio', 'premium')  # 'classico' ou 'premium'
            comissao = comissao_min if tipo_anuncio == 'classico' else comissao_max
        
        # Obter dados de precificação do contexto
        impostos = ctx.get('impostos', 0.12)  # Padrão 12%
        tacos = ctx.get('tacos', 0.05)  # Investimento em publicidade: padrão 5%
        margem_contribuicao = ctx.get('margem_contribuicao', 0.10)  # M.C.: padrão 10%
        lucro = ctx.get('lucro', 0.05)  # Lucro: padrão 5%
        
        # Calcular custo total (produto + frete)
        custo_total = self.calculate_total_cost(cost_price, shipping_cost)
        
        # Calcular denominador (1 - soma de todos os percentuais)
        # Preço = Custo / (1 - %comissao - %impostos - %tacos - %mc - %lucro)
        soma_percentuais = comissao + impostos + tacos + margem_contribuicao + lucro
        
        denominador = 1 - soma_percentuais
        
        # Calcular preço base
        preco_base = custo_total / denominador
        
        # Aplicar arredondamento .99
        preco_arredondado = self.apply_rounding(preco_base, ctx)
        
        # Garantir não-negativo
        return self.ensure_non_negative(preco_arredondado)
    
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
        Mostra todos os componentes de custo e margem.
        """
        if not ctx:
            return super().get_breakdown(cost_price, shipping_cost, ctx)
        
        # Obter percentual de comissão: priorizar commission_percent se fornecido
        if 'commission_percent' in ctx and ctx.get('commission_percent') is not None:
            # Usar comissão diretamente informada
            comissao = float(ctx.get('commission_percent', 0.15))
            tipo_anuncio = f"{comissao*100:.1f}%"  # Exibir percentual no breakdown
        else:
            # Fallback: usar lógica antiga (min/max)
            comissao_min = ctx.get('comissao_min', 0.12)
            comissao_max = ctx.get('comissao_max', 0.17)
            tipo_anuncio = ctx.get('tipo_anuncio', 'premium')
            comissao = comissao_min if tipo_anuncio == 'classico' else comissao_max
        
        # Obter configurações
        impostos = ctx.get('impostos', 0.08)
        tacos = ctx.get('tacos', 0.05)
        margem_contribuicao = ctx.get('margem_contribuicao', 0.15)
        lucro = ctx.get('lucro', 0.10)
        custo_total = self.calculate_total_cost(cost_price, shipping_cost)
        preco_final = self.get_listing_price(cost_price, shipping_cost, ctx)
        
        # Calcular valores absolutos
        valor_comissao = preco_final * comissao
        valor_impostos = preco_final * impostos
        valor_tacos = preco_final * tacos
        valor_mc = preco_final * margem_contribuicao
        valor_lucro = preco_final * lucro
        
        steps = [
            {"label": "💰 Custo do produto", "value": cost_price},
            {"label": "📦 Custo de frete", "value": shipping_cost},
            {"label": "➕ Custo total (produto + frete)", "value": custo_total},
            {"label": "─────────────────────", "value": 0},
            {"label": f"🏪 Comissão ML {tipo_anuncio.title()} ({comissao*100:.1f}%)", "value": valor_comissao},
            {"label": f"🧾 Impostos ({impostos*100:.1f}%)", "value": valor_impostos},
            {"label": f"📢 Investimento Publicidade/TACOS ({tacos*100:.1f}%)", "value": valor_tacos},
            {"label": f"📊 Margem de Contribuição ({margem_contribuicao*100:.1f}%)", "value": valor_mc},
            {"label": f"💵 Lucro ({lucro*100:.1f}%)", "value": valor_lucro},
            {"label": "─────────────────────", "value": 0},
            {"label": "🏷️ PREÇO FINAL", "value": preco_final},
        ]
        
        notes = [
            f"Canal: Mercado Livre ({tipo_anuncio.title()})",
            f"Soma de percentuais: {(comissao + impostos + tacos + margem_contribuicao + lucro)*100:.1f}%",
            f"Markup aplicado: {((preco_final / custo_total - 1)*100):.1f}%",
            "Clássico ML: 10-14% comissão | Premium ML: 15-19% comissão"
        ]
        
        return PriceBreakdown(steps=steps, notes=notes)
