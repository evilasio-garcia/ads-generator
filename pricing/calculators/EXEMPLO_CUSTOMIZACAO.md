# Exemplo de Customização - Mercado Livre

Este arquivo mostra como customizar os métodos da calculadora do Mercado Livre.

## Exemplo 1: Customizar Preço de Lista por Categoria

```python
def get_listing_price(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> float:
    """Preço customizado baseado em categoria do produto"""
    
    # Obter categoria do contexto (se fornecida)
    categoria = ctx.get('categoria') if ctx else None
    
    # Ajustar markup baseado na categoria
    if categoria == 'eletronicos':
        # Eletrônicos: markup mais baixo (mercado competitivo)
        custom_ctx = {**(ctx or {}), 'markup': 1.8}
    elif categoria == 'moda':
        # Moda: markup mais alto (menos competitivo)
        custom_ctx = {**(ctx or {}), 'markup': 2.5}
    else:
        # Usar padrão
        custom_ctx = ctx
    
    return super().get_listing_price(cost_price, shipping_cost, custom_ctx)
```

**Como usar:**
```python
calc = PriceCalculatorFactory.get('mercadolivre')
price = calc.get_listing_price(100.0, 15.0, ctx={'categoria': 'eletronicos'})
# Resultado: preço com markup 180% em vez de 220%
```

---

## Exemplo 2: Tiers de Atacado Personalizados

```python
def get_wholesale_tiers(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> List[WholesaleTier]:
    """Faixas de atacado customizadas para Mercado Livre"""
    
    listing_price = self.get_listing_price(cost_price, shipping_cost, ctx)
    
    # Tiers customizados: 3, 6, 12, 24+ unidades
    tiers = [
        WholesaleTier(tier=1, min_quantity=3, price=self.apply_rounding(listing_price * 0.93, ctx)),
        WholesaleTier(tier=2, min_quantity=6, price=self.apply_rounding(listing_price * 0.88, ctx)),
        WholesaleTier(tier=3, min_quantity=12, price=self.apply_rounding(listing_price * 0.82, ctx)),
        WholesaleTier(tier=4, min_quantity=24, price=self.apply_rounding(listing_price * 0.75, ctx)),
    ]
    
    return tiers
```

---

## Exemplo 3: Preço Agressivo com Lógica de Estoque

```python
def get_aggressive_price(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> float:
    """Preço agressivo ajustado por estoque disponível"""
    
    listing_price = self.get_listing_price(cost_price, shipping_cost, ctx)
    
    # Obter estoque do contexto
    estoque = ctx.get('estoque', 100) if ctx else 100
    
    # Quanto menor o estoque, menor o desconto (manter margem)
    if estoque < 10:
        # Estoque baixo: desconto menor (8%)
        discount = 0.08
    elif estoque < 50:
        # Estoque médio: desconto padrão (12%)
        discount = 0.12
    else:
        # Estoque alto: desconto agressivo (15%)
        discount = 0.15
    
    aggressive = listing_price * (1 - discount)
    return self.ensure_non_negative(self.apply_rounding(aggressive, ctx))
```

**Como usar:**
```python
calc = PriceCalculatorFactory.get('mercadolivre')
price = calc.get_aggressive_price(100.0, 10.0, ctx={'estoque': 5})
# Resultado: desconto de apenas 8% (estoque baixo)
```

---

## Exemplo 4: Preço Promocional por Evento

```python
def get_promo_price(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> float:
    """Preço promocional para eventos especiais"""
    
    listing_price = self.get_listing_price(cost_price, shipping_cost, ctx)
    
    # Obter evento do contexto
    evento = ctx.get('evento') if ctx else None
    
    # Descontos por evento
    if evento == 'black_friday':
        discount = 0.25  # 25% off
    elif evento == 'cyber_monday':
        discount = 0.22  # 22% off
    elif evento == 'natal':
        discount = 0.20  # 20% off
    else:
        discount = self.PROMO_DISCOUNT  # Padrão 18%
    
    promo = listing_price * (1 - discount)
    return self.ensure_non_negative(self.apply_rounding(promo, ctx))
```

---

## Exemplo 5: Breakdown Detalhado com Comissões ML

```python
def get_breakdown(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> PriceBreakdown:
    """Breakdown detalhado incluindo comissões específicas do ML"""
    
    # Obter breakdown padrão
    breakdown = super().get_breakdown(cost_price, shipping_cost, ctx)
    
    # Adicionar steps extras específicos do Mercado Livre
    listing_price = self.get_listing_price(cost_price, shipping_cost, ctx)
    comissao_ml = listing_price * 0.15  # 15% de comissão
    
    extra_steps = [
        {"label": "Comissão Mercado Livre (15%)", "value": comissao_ml},
        {"label": "Taxa de cartão (~3%)", "value": listing_price * 0.03},
    ]
    
    # Inserir após os steps existentes
    breakdown.steps.extend(extra_steps)
    
    # Adicionar notas específicas
    breakdown.notes.append("Mercado Livre: Considere frete grátis para aumentar conversão")
    breakdown.notes.append("ML Premium: Comissão pode ser menor (12-13%)")
    
    return breakdown
```

---

## Combinando Múltiplas Customizações

```python
# Chamada completa com contexto rico
calc = PriceCalculatorFactory.get('mercadolivre')

ctx = {
    'categoria': 'eletronicos',
    'estoque': 5,
    'evento': 'black_friday',
    'regiao': 'sudeste',
    'reputacao': 'ouro'
}

# Obter todos os preços customizados
listing = calc.get_listing_price(100.0, 15.0, ctx)
aggressive = calc.get_aggressive_price(100.0, 15.0, ctx)
promo = calc.get_promo_price(100.0, 15.0, ctx)
tiers = calc.get_wholesale_tiers(100.0, 15.0, ctx)
breakdown = calc.get_breakdown(100.0, 15.0, ctx)
```

---

## Dicas de Customização

1. **Use `ctx` (contexto)** para passar informações adicionais sem alterar a assinatura dos métodos
2. **Chame `super()`** quando quiser aproveitar o comportamento padrão
3. **Sempre use `self.apply_rounding()`** para manter preços com .99
4. **Sempre use `self.ensure_non_negative()`** para evitar preços negativos
5. **Acesse constantes da classe** via `self.DEFAULT_MARKUP`, `self.DEFAULT_TAX_RATE`, etc
6. **Métodos auxiliares disponíveis:**
   - `self.calculate_total_cost(cost_price, shipping_cost)` → custo total
   - `self.calculate_base_price(total_cost, markup, tax_rate)` → preço base
   - `self.apply_rounding(price, ctx)` → arredonda para .99
   - `self.ensure_non_negative(price)` → garante preço ≥ 0

---

## Estrutura Recomendada

```python
def get_listing_price(self, cost_price: float, shipping_cost: float = 0.0, ctx: Optional[Dict[str, Any]] = None) -> float:
    # 1. Extrair dados do contexto
    categoria = ctx.get('categoria') if ctx else None
    
    # 2. Aplicar sua lógica customizada
    if categoria == 'especial':
        # Fazer algo diferente
        custom_markup = 3.0
        custom_ctx = {**(ctx or {}), 'markup': custom_markup}
        return super().get_listing_price(cost_price, shipping_cost, custom_ctx)
    
    # 3. Ou criar cálculo totalmente customizado
    total_cost = self.calculate_total_cost(cost_price, shipping_cost)
    base_price = self.calculate_base_price(total_cost, 2.5, 0.15)
    rounded_price = self.apply_rounding(base_price, ctx)
    
    # 4. Sempre garantir não-negativo
    return self.ensure_non_negative(rounded_price)
```
