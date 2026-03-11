# Sistema de Frete e Variantes — Documentacao Tecnica

> **Proposito:** Documentar o funcionamento interno do sistema de frete entre abas de variantes
> (simples, kit2-kit5) em `static/main.html`. Este documento existe para evitar alucinacoes
> em futuras features, refatoracoes ou correcoes nesta area do sistema.
>
> **Ultima atualizacao:** 2026-03-10

---

## Indice

1. [Visao geral do fluxo](#visao-geral-do-fluxo)
2. [Estruturas de dados](#estruturas-de-dados)
3. [Ciclo de vida do frete numa troca de aba](#ciclo-de-vida-do-frete-numa-troca-de-aba)
4. [Botao "Buscar no Marketplace"](#botao-buscar-no-marketplace)
5. [Edicao manual do frete](#edicao-manual-do-frete)
6. [Persistencia e F5](#persistencia-e-f5)
7. [SSE freight_updated (publicacao ML)](#sse-freight_updated)
8. [Armadilhas documentadas (bugs ja corrigidos)](#armadilhas-documentadas)
9. [Regras invariantes](#regras-invariantes)
10. [Referencia de funcoes](#referencia-de-funcoes)

---

## 1. Visao geral do fluxo

O frete de cada variante e **independente**. O simples pode ter frete 0 (custo < R$ 78,99)
enquanto kit2 tem 19,31 e kit3-kit5 tem 22,45. Cada variante consulta a API de frete
com seu proprio custo de decisao (`custo_do_kit * 2`).

```
                          ┌──────────────────────────────────┐
                          │       tinyShippingCost.value      │
                          │    (campo UI — fonte da verdade)  │
                          └───────┬──────────────┬───────────┘
                                  │              │
                     captureActive...()     hydrate...()
                        (campo → snapshot)  (snapshot → campo)
                                  │              │
                          ┌───────▼──────────────▼───────────┐
                          │  variantStore[key]                │
                          │    .shippingCostSnapshot          │
                          │  (snapshot em memoria por aba)    │
                          └───────┬──────────────┬───────────┘
                                  │              │
                        persistState()   restoreWorkspace...()
                          (memoria → DB)  (DB → cache → snapshot)
                                  │              │
                          ┌───────▼──────────────▼───────────┐
                          │  shippingCostCache               │
                          │  { "SKU:marketplace:variant": N } │
                          │  (cache persistido no workspace) │
                          └──────────────────────────────────┘
```

**Principio fundamental:** A interface (campo `tinyShippingCost`) e a fonte da verdade
para o frete da variante ativa. Snapshots e caches sao copias de sincronizacao.

---

## 2. Estruturas de dados

### 2.1 variantStore (linha ~7367)

Objeto em memoria com uma entrada por variante. Cada entrada tem:

| Campo                  | Tipo   | Descricao                                                       |
|------------------------|--------|-----------------------------------------------------------------|
| `shippingCostSnapshot` | string | Frete capturado da UI (ex: `"22.45"`)                           |
| `costPriceSnapshot`    | string | Custo capturado/derivado (kits: `baseCost * qty`)               |
| `widthSnapshot`        | string | Largura capturada/derivada                                      |
| `weightSnapshot`       | string | Peso capturado/derivado                                         |
| `derivedSignature`     | string | Hash `"qty|cost|width|weight"` para detectar mudancas           |
| `pricesDirty`          | bool   | Se precos precisam recalcular                                   |
| Campos de preco...     |        | `announcePriceMin/Max`, `aggressivePriceMin/Max`, `wholesaleRows` |

**CRITICO:** `shippingCostSnapshot` **NAO e derivado** do simples.
Custo, largura e peso sao derivados em `ensureKitDerivedState()`, mas frete nao.
Cada kit tem seu proprio frete independente.

### 2.2 shippingCostCache (linha ~2507)

```javascript
let shippingCostCache = {};
// Chave: "SKU_NORMALIZADO:marketplace:variante"
// Exemplo: "NEWGD60C7:mercadolivre:kit3"
// Valor: numero (ex: 22.45)
```

**Onde e preenchido:**
- `autoFillShippingCost()` apos chamada de API bem-sucedida (linha ~6025)
- `restoreWorkspaceVersionedState()` a partir do workspace persistido (linha ~3683)
- Handler SSE `freight_updated` (linha ~9967)
- Auto-fill no load do Tiny (linha ~4497)

**Onde e consultado:**
- `autoFillShippingCost()` antes de fazer API call (linha ~5984)
- `restoreWorkspaceVersionedState()` para restaurar snapshots de kit (linha ~3856)

### 2.3 shippingCostLocked (linha ~2508)

Flag global que indica se o frete foi definido externamente (manual ou ML).

| Evento                             | Valor    |
|------------------------------------|----------|
| Usuario edita frete manualmente    | `true`   |
| SSE `freight_updated` (ML publica) | `true`   |
| Workspace restaurado com frete     | `true`   |
| Botao "Buscar no Marketplace"      | `false`  |
| Troca de marketplace               | `false`  |

**Efeito:** Quando `true`, `autoPricing` NAO auto-preenche frete para variante simples.
Para kits, o travamento e **ignorado** — kits sempre recalculam se frete <= 0.

---

## 3. Ciclo de vida do frete numa troca de aba

Ao clicar numa aba de variante (ex: simples → kit3), `switchVariantTab()` executa:

```
1. variantSwitchInProgress = true
   ↓
2. captureActiveVariantVolatileState()
   → Salva tinyShippingCost.value no snapshot da aba ANTERIOR
   → history ainda aponta para a aba anterior
   ↓
3. activeVariantKey = "kit3"
   history = variantStore.kit3
   ↓
4. applyHistoryToUi()
   → Atualiza titulos, descricoes, FAQs, cards
   → EFEITO COLATERAL: renderFAQFromState() e renderCardsFromState()
     chamam persistState() internamente!
   → MAS: persistState() tem guard: se variantSwitchInProgress,
     NAO chama captureActiveVariantVolatileState()
   ↓
5. hydrateActiveVariantVolatileState()
   → ensureKitDerivedState() calcula custo/largura/peso derivados
   → tinyShippingCost.value = history.shippingCostSnapshot || ""
   → Se snapshot vazio, campo fica vazio (frete sera calculado por autoPricing)
   ↓
6. autoPricing() [await]
   → Le tinyShippingCost.value
   → Se vazio (0) e ML e custo*2 > 78.99: chama autoFillShippingCost()
   → autoFillShippingCost() faz API call, seta campo, atualiza cache
   → Retorna frete calculado → autoPricing usa para calcular precos
   ↓
7. captureActiveVariantVolatileState() [explicito]
   → Salva campo (agora correto) no snapshot da aba NOVA
   ↓
8. persistState("variant_nav")
   → NAO chama capture (variantSwitchInProgress = true)
   → Agenda flush debounced (600ms) para salvar no backend
   ↓
9. variantSwitchInProgress = false
```

### Por que o guard em persistState e critico

Sem o guard (bug corrigido em 2026-03-10), o fluxo era:

```
Passo 4: applyHistoryToUi() → renderFAQFromState() → persistState("faq_render")
                                                          ↓
                                            captureActiveVariantVolatileState()
                                                          ↓
                                    history (kit3).shippingCostSnapshot = tinyShippingCost.value
                                                          ↓
                                    MAS tinyShippingCost.value AINDA TEM o valor da aba anterior!
                                                          ↓
                                    kit3 contaminado com frete de kit2 ← BUG
```

**Regra:** `persistState()` NUNCA deve chamar `captureActiveVariantVolatileState()`
durante `variantSwitchInProgress`. A troca de aba faz a captura explicitamente nos
momentos corretos (passos 2 e 7 acima).

---

## 4. Botao "Buscar no Marketplace"

Handler: `btnAutoFillShipping.addEventListener('click', async () => { ... })` (linha ~6051)

### Fluxo completo

```
1. shippingCostLocked = false
   → Destrava para permitir auto-preenchimento futuro por autoPricing
   ↓
2. autoFillShippingCost({
     forceRefresh: true,            // Ignora cache
     usePromoReference: true,       // Usa preco promo como referencia
     shippingDecisionCostBase: getShippingDecisionBaseCost(costPrice)
   })
   → Faz API call com custo de decisao (custo * 2)
   → Seta tinyShippingCost.value com resultado
   → Atualiza shippingCostCache
   ↓
3. if (resultado valido):
   → Atualiza snapshot da variante ativa
   → persistState("shipping_button_refresh")
   → autoPricing() para recalcular precos
```

### Armadilha corrigida: Number(null) = 0

Antes da correcao, o handler nao passava `shippingDecisionCostBase`. Dentro de
`autoFillShippingCost`, o check era:

```javascript
// ANTES (BUG):
const decisionCostBase = Number.isFinite(Number(shippingDecisionCostBase))
  ? Number(shippingDecisionCostBase)  // Number(null) = 0 → decisionCostBase = 0!
  : baseCostField;
```

`Number(null)` retorna `0` em JavaScript, e `Number.isFinite(0)` e `true`.
Resultado: `decisionCostBase = 0`, e o guard `if (decisionCostBase <= 0) return null`
impedia qualquer chamada de API. O botao estava **100% quebrado**.

```javascript
// DEPOIS (CORRIGIDO):
const decisionCostBase = (shippingDecisionCostBase != null && Number.isFinite(Number(shippingDecisionCostBase)))
  ? Number(shippingDecisionCostBase)
  : baseCostField;
```

### Por que o handler precisa fazer 3 coisas alem de chamar autoFillShippingCost

`autoFillShippingCost()` seta `tinyShippingCost.value` programaticamente. Setar `.value`
via JavaScript **NAO dispara** eventos `change` ou `input`. Portanto:

1. **Snapshot nao e atualizado** → o handler faz manualmente
2. **persistState nao e chamado** → o handler chama
3. **autoPricing nao e disparado** → o handler chama

Sem essas 3 acoes extras, o botao atualizaria o campo visual mas:
- Ao trocar de aba, o frete voltaria ao valor anterior (snapshot desatualizado)
- Os precos nao seriam recalculados
- O workspace nao seria salvo

---

## 5. Edicao manual do frete

Handler: `tinyShippingCost.addEventListener('change', () => { ... })` (linha ~5543)

### Fluxo

```
1. if (variantSwitchInProgress) return;  ← Guard contra captura durante troca
   ↓
2. shippingCostLocked = true (se valor > 0)
   ↓
3. Atualiza snapshot da variante ativa
   ↓
4. persistState("tiny_shipping_edit")
   ↓
5. autoPricing() com o novo frete
```

**Diferenca do botao:** A edicao manual usa evento `change` (disparado pelo browser),
entao todas as acoes (snapshot, persist, pricing) estao dentro do handler.
O botao precisa fazer essas acoes manualmente porque `.value = X` nao dispara `change`.

---

## 6. Persistencia e F5

### Salvamento (persistState → flushWorkspacePersist)

```
captureActiveVariantVolatileState()  → Copia campo UI para snapshot
                                        ↓
                                    flushWorkspacePersist()  (debounced 600ms)
                                        ↓
                                    POST /api/sku/workspace/save
                                    body: {
                                      base_state: {
                                        shipping_cost_cache: shippingCostCache,
                                        ...
                                      },
                                      versioned_state: {
                                        variants: {
                                          simple: { shippingCostSnapshot, ... },
                                          kit2: { shippingCostSnapshot, ... },
                                          ...
                                        }
                                      }
                                    }
```

### Restauracao (apos F5)

```
POST /api/sku/workspace/load → workspace
    ↓
restoreWorkspaceVersionedState()
    ↓
1. Restaura shippingCostCache do base_state
2. Para cada kit (nao simples):
     cacheKey = "SKU:marketplace:kitN"
     se cache[cacheKey] existe:
       variantStore[kitN].shippingCostSnapshot = cache[cacheKey]
3. switchVariantTab("simple", { skipPersist: true })
     → hydrate simples com snapshot salvo
```

**Por que kits restauram do cache e nao do versioned_state:**

O `versioned_state` salva os snapshots de cada variante, mas `ensureKitDerivedState()`
sobrescreve `costPriceSnapshot`, `widthSnapshot` e `weightSnapshot` (derivando do simples).
O `shippingCostSnapshot` NAO e sobrescrito por `ensureKitDerivedState`, mas pode estar
vazio se a troca de aba anterior nao salvou corretamente. O cache e a fonte mais confiavel
porque e escrito explicitamente por `autoFillShippingCost()` e pelo handler SSE.

---

## 7. SSE freight_updated

Quando o backend publica um anuncio no ML e detecta divergencia de frete:

```
Backend:
  _persist_freight()  → Salva frete ML no DB
  _emit_ml_event("freight_updated", new_freight=15.90, ...)
                                        ↓
Frontend (handleEvent):
  1. tinyShippingCost.value = "15.90"
  2. shippingCostLocked = true
  3. Snapshot da variante ativa atualizado
  4. Cache atualizado
  5. autoPricing() recalcula precos
  6. persistState() salva workspace
  7. return false  ← NAO fecha o stream SSE
```

**Decisao de design:** O handler atualiza a variante **ativa no momento**,
nao necessariamente a variante "simple". Se o usuario estiver na aba kit3 quando o ML
envia o evento, o frete e salvo no snapshot de kit3.

---

## 8. Armadilhas documentadas (bugs ja corrigidos)

### 8.1 Contaminacao de frete entre abas via persistState

**Data:** 2026-03-10
**Causa:** `renderFAQFromState()` e `renderCardsFromState()` (chamados por `applyHistoryToUi()`)
faziam `persistState()` como efeito colateral. `persistState()` chamava
`captureActiveVariantVolatileState()`, que lia `tinyShippingCost.value` (ainda com valor
da aba anterior) e gravava no snapshot da aba nova.
**Correcao:** Guard em `persistState`: `if (!variantSwitchInProgress) { captureActiveVariantVolatileState(); }`

### 8.2 Botao "Buscar no Marketplace" 100% inoperante

**Data:** 2026-03-10
**Causa:** `Number(null) = 0` passava no check `Number.isFinite(0)`, fazendo
`decisionCostBase = 0` → retorno antecipado sem API call.
**Correcao:** Guard `shippingDecisionCostBase != null` adicionado ao check.
Handler do botao reescrito para: (a) passar `shippingDecisionCostBase`, (b) atualizar
snapshot, (c) chamar `persistState`, (d) chamar `autoPricing`.

### 8.3 Frete de kits vazio apos F5

**Data:** 2026-03-10
**Causa:** `restoreWorkspaceVersionedState()` nao restaurava snapshots de frete de kits
a partir do cache. Apos F5, todos os kits ficavam com `shippingCostSnapshot = ""`.
**Correcao:** Loop em `VARIANT_DEFS` restaurando snapshot de cada kit a partir do cache.

### 8.4 Cache key inconsistente (faltava sufixo de variante)

**Data:** 2026-03-10
**Causa:** O auto-fill no load do Tiny usava cache key `SKU:marketplace` (sem `:simple`),
enquanto `autoFillShippingCost()` usava `SKU:marketplace:simple`. Valores nunca batiam.
**Correcao:** Adicionado `:simple` a cache key no load do Tiny.

### 8.5 resetPriceHistoryState zerava shippingCostSnapshot

**Data:** 2026-03-10
**Causa:** Quando `ensureKitDerivedState()` detectava mudanca de assinatura, chamava
`resetPriceHistoryState()` que zerava todos os campos — incluindo `shippingCostSnapshot`.
**Correcao:** Removido `shippingCostSnapshot = ""` de `resetPriceHistoryState()`.

---

## 9. Regras invariantes

Estas regras DEVEM ser mantidas em qualquer mudanca futura nesta area:

1. **`captureActiveVariantVolatileState()` NUNCA deve ser chamado durante
   `variantSwitchInProgress` exceto nos pontos explicitos de `switchVariantTab()`
   (passo 2: captura aba anterior, passo 7: captura aba nova apos autoPricing).**

2. **`shippingCostSnapshot` NAO e derivado.** Diferente de `costPriceSnapshot`,
   `widthSnapshot` e `weightSnapshot` (que sao derivados do simples por
   `ensureKitDerivedState`), o frete e independente por variante.

3. **`resetPriceHistoryState()` NAO deve resetar `shippingCostSnapshot`.** Frete
   sobrevive a mudancas de assinatura de kit.

4. **Qualquer funcao que sete `tinyShippingCost.value` programaticamente DEVE tambem:**
   - Atualizar o snapshot da variante ativa
   - Chamar `persistState()` para agendar salvamento
   - Chamar `autoPricing()` para recalcular precos

   Isso porque `.value = X` NAO dispara eventos `change`/`input` no DOM.

5. **Cache key de frete SEMPRE inclui a variante:** `SKU:marketplace:variant`
   (ex: `NEWGD60C7:mercadolivre:kit3`). Nunca omitir o sufixo de variante.

6. **`autoFillShippingCost()` deve receber `shippingDecisionCostBase` quando chamado
   pelo botao ou por `autoPricing`.** Se null/undefined, o fallback para `baseCostField`
   e seguro, mas o custo enviado a API sera diferente do que `autoPricing` usaria
   (falta a multiplicacao por 2 de `getShippingDecisionBaseCost`).

7. **`shippingCostLocked` afeta TODAS as variantes igualmente (simples e kits).**
   Quando o usuario edita o frete manualmente (inclusive digitando 0), `shippingCostLocked = true`
   e `autoPricing` NAO sobrescreve o valor — independente de ser simples ou kit.
   O lock so e desativado pelo botao "Buscar no Marketplace", troca de marketplace,
   ou **troca de aba** quando a nova variante tem snapshot de frete vazio.
   Na troca de aba, `switchVariantTab` sincroniza o lock com o snapshot da nova variante:
   se o snapshot tem um valor (inclusive "0"), o lock e ativado; se o snapshot e vazio,
   o lock e desativado para permitir auto-fill.

8. **O guard `Number(null)` e uma armadilha de JavaScript.** Sempre usar
   `val != null && Number.isFinite(Number(val))` ao verificar parametros opcionais
   numericos. `Number(null) === 0` e `Number.isFinite(0) === true`.

9. **Frete pode ser qualquer valor >= 0 digitado manualmente.** Ao editar o campo de frete
   com qualquer valor valido (incluindo 0), `shippingCostLocked = true` e ativado. Isso impede
   que `autoPricing` sobrescreva o valor, tanto para simples quanto para kits.
   A condicao de lock e: `val !== '' && Number.isFinite(parseFloat(val)) && parseFloat(val) >= 0`.

10. **Frete zero e valido — NAO e campo vazio.** A validacao de publicacao
    (`validateWorkspaceForMlPublish`) verifica se o campo esta **vazio** (string vazia),
    nao se o valor e zero. Zero e um valor legitimo (frete gratis para itens ate R$ 78,99).

11. **Alerta de frete zero em anuncios caros.** Se o frete e 0 mas o preco do anuncio
    e maior que R$ 78,99, o sistema exibe um modal de confirmacao (`zeroFreightModal`)
    antes de publicar. O usuario pode cancelar ou confirmar com "Ok, estou ciente".
    Se o preco <= R$ 78,99, a publicacao segue sem alerta (frete gratis e esperado).

---

## 10. Referencia de funcoes

| Funcao                                | Linha aprox. | Responsabilidade                                              |
|---------------------------------------|-------------|---------------------------------------------------------------|
| `createEmptyVariantHistory()`         | 7367        | Cria estado vazio para uma variante                           |
| `captureActiveVariantVolatileState()` | 7513        | Campo UI → snapshot em memoria                                |
| `hydrateActiveVariantVolatileState()` | 7564        | Snapshot em memoria → campo UI                                |
| `ensureKitDerivedState(key)`          | 7490        | Calcula custo/largura/peso de kit (NAO frete)                 |
| `switchVariantTab(key, opts)`         | 7637        | Orquestra troca completa de aba                               |
| `persistState(action)`               | 7840        | Captura + agenda flush debounced (600ms)                      |
| `autoFillShippingCost(opts)`          | 5961        | Busca frete ML via API ou cache                               |
| `autoPricing(opts)`                   | 5189        | Calcula precos; auto-preenche frete se necessario             |
| `getShippingDecisionBaseCost(cost)`   | 5954        | `cost * 2` para regra de decisao                              |
| `resetPriceHistoryState(state)`       | 7450        | Limpa historico de precos (preserva frete)                    |
| `restoreWorkspaceVersionedState()`    | 3610        | Restaura workspace completo apos F5/load                     |
| `btnAutoFillShipping` handler         | 6051        | Botao "Buscar no Marketplace"                                 |
| `tinyShippingCost` change handler     | 5543        | Edicao manual do campo de frete                               |
| SSE `freight_updated` handler         | 9951        | Atualiza frete apos publicacao ML                             |

---

## Testes automatizados que protegem esta area

| Arquivo de teste                                     | O que cobre                                                           |
|------------------------------------------------------|-----------------------------------------------------------------------|
| `tests/gui/test_newgd60c7_reference_sanity_gui.py`   | Valores exatos de frete em todas as abas + F5 (18 passos x 2 runs)    |
| `tests/gui/test_shipping_button_gui.py`              | Botao "Buscar no Marketplace": reset, pricing, snapshot, F5, random   |
| `tests/gui/test_sku_variants_gui.py`                 | Consistencia geral de variantes e precos                              |
| `tests/gui/test_dual_sku_reference_with_price_tabs_gui.py` | Troca entre dois SKUs com precos e abas                         |
