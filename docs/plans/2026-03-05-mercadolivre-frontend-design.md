# Design — Interfaces Frontend para Publicação no Mercado Livre

**Data:** 2026-03-05
**Status:** Aprovado
**Referência:** `docs/plans/2026-03-05-mercadolivre-publish-design.md`

---

## Visão Geral

Este documento descreve as interfaces, componentes e comportamentos de UI necessários para que o fluxo de publicação de anúncios no Mercado Livre seja 100% utilizável dentro do sistema adsGenerator.

O sistema utiliza Tailwind CSS + Inter, com cor primária Indigo-600 e um único arquivo `static/main.html` contendo todo o HTML, CSS e JS. As novas interfaces seguem rigorosamente os padrões visuais existentes.

---

## Seção 1: Botão Flutuante de Publicação (rail direito do result card)

### Posicionamento

Espelho do `.variant-tabs-rail` existente (abas de variante à esquerda), mas ancorado ao lado direito do `#resultCard`. Fica fora da área do card, sticky ao grupo de resultado enquanto o usuário rola a página.

```
[variant-tabs-rail] ← [  #resultCard content  ] → [publish-rail]
```

Estrutura HTML:

```html
<aside class="publish-rail">
  <div class="publish-rail-inner">
    <button class="publish-rail-btn" data-marketplace="mercadolivre">
      <span class="publish-rail-label">Publicar no ML</span>
    </button>
  </div>
</aside>
```

CSS base (espelho do `.variant-tabs-rail`):

```css
.publish-rail {
  position: absolute;
  right: -54px;      /* espelha o left: -54px do variant-tabs-rail */
  top: 0;
  bottom: 0;
  width: 0;
  overflow: visible;
  z-index: 8;
}

.publish-rail-inner {
  position: sticky;
  top: 84px;
  transform: translateX(0);   /* abre para a direita */
  pointer-events: auto;
  display: flex;
  align-items: flex-start;
}
```

### Visual do botão — estado normal

- **Forma:** retângulo arredondado dos dois lados (não apenas um como as abas de variante) — identidade de "botão", não de "aba"
- **Fundo:** `#FFE600` (amarelo Mercado Livre)
- **Texto:** `"Publicar no ML"` rotacionado 90° (bottom-to-top), `font-bold`, cor `#2D73E2` (azul ML)
- **Bordas:** `border-radius: 0.7rem` em todos os cantos
- **Sombra:** `box-shadow` azul suave para destacar do fundo
- **Hover:** leve escurecimento do amarelo + sombra azul mais intensa
- **Disabled:** `opacity: 0.45; cursor: not-allowed` — aplicado quando já há um job em andamento

```css
.publish-rail-btn {
  background: #FFE600;
  color: #2D73E2;
  border: 2px solid #F0D800;
  border-radius: 0.7rem;
  padding: 0.55rem 0.34rem;
  min-height: 4.2rem;
  font-weight: 700;
  box-shadow: 2px 2px 10px rgba(45, 115, 226, 0.20);
  transition: background 0.18s, box-shadow 0.2s, opacity 0.18s;
}

.publish-rail-btn:hover {
  background: #F0D800;
  box-shadow: 2px 4px 16px rgba(45, 115, 226, 0.35);
}

.publish-rail-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  pointer-events: none;
}

.publish-rail-label {
  writing-mode: vertical-rl;
  text-orientation: mixed;
  transform: rotate(180deg);
  white-space: nowrap;
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0.015em;
}
```

### Visibilidade condicional

O botão é exibido somente quando o `<select id="marketplace">` tem o valor `"mercadolivre"`. Ao trocar o marketplace, o botão se oculta (ou no futuro adapta cor/texto ao marketplace alvo).

### Leitura do listing_type_id

O botão não distingue aba ativa internamente — ao ser clicado, o JS lê qual `.price-tab-btn` está `active` no momento:
- Aba `% Min (Clássico)` ativa → `listing_type_id = "gold_special"`
- Aba `% Max (Premium)` ativa → `listing_type_id = "gold_pro"`

---

## Seção 2: Painel de Progresso (expansão inline do botão)

### Comportamento de transição

Ao clicar "Publicar no ML":
1. O botão some com animação `fade-out`
2. Um painel (`publish-panel`) expande no mesmo rail com animação `slide-in` da direita
3. O painel tem largura fixa (~280px) e altura mínima suficiente para exibir todas as etapas
4. O restante do sistema permanece totalmente utilizável

```css
.publish-panel {
  width: 280px;
  background: #FFF9E0;          /* amarelo muito claro, identidade ML */
  border: 2px solid #FFE600;
  border-radius: 0.9rem;
  overflow: hidden;
  box-shadow: 4px 6px 20px rgba(45, 115, 226, 0.18);
  animation: publishPanelSlideIn 0.28s ease-out;
}

@keyframes publishPanelSlideIn {
  from { opacity: 0; transform: translateX(16px); }
  to   { opacity: 1; transform: translateX(0); }
}
```

### Layout interno do painel

```
┌──────────────────────────────┐
│  [loader] Publicando no ML   │  <- header ML (fundo #FFE600, texto #2D73E2 bold)
├──────────────────────────────┤
│  SKU: PROD-123               │  <- contexto
│  Tipo: Anúncio Clássico      │
├──────────────────────────────┤
│  ✅ Credenciais verificadas  │
│  ✅ Imagens enviadas (4)     │
│  ⏳ Criando anúncio...       │  <- etapa atual (loader + texto animado)
│  ○  Verificando frete        │  <- pendentes (muted)
│  ○  Ativando anúncio         │
└──────────────────────────────┘
```

### Estados visuais das etapas SSE

| Estado | Ícone | Cor do texto |
|---|---|---|
| Concluída | ✅ check SVG | Verde `#10b981` |
| Em andamento | loader giratório (`.loader`) | Azul `#2D73E2` |
| Pendente | círculo vazio | Cinza muted `#9ca3af` |
| Erro | ✗ SVG | Vermelho `#ef4444` |

Mapeamento de steps SSE para labels de UI:

| `step` (SSE) | Label exibida |
|---|---|
| `token_refresh` | Verificando credenciais ML... |
| `downloading_images` | Baixando imagens do Drive... |
| `uploading_images` | Enviando imagens ao Mercado Livre... |
| `creating_listing` | Criando anúncio no ML... |
| `checking_freight` | Consultando custo de frete... |
| `adjusting_price` | Frete divergente — recalculando preços... |
| `updating_listing` | Atualizando preço no anúncio... |
| `notifying_whatsapp` | Notificando divergência via WhatsApp... |
| `activating` | Ativando anúncio... |
| `done` | Anúncio publicado com sucesso! |
| `error` | Falha em: `<step>` |

### Estado de sucesso

1. Header muda para fundo verde suave + texto "Anúncio publicado!"
2. Link clicável com `listing_url` abre em nova aba
3. Após ~2s: painel fecha com `fade-out`, botão retorna com `fade-in`
4. `showToast("Anúncio publicado! Ver anúncio ↗", "success", 5000)`

### Estado de erro

1. Header muda para fundo vermelho suave + texto "Falha na publicação"
2. Etapa com erro destacada em vermelho
3. Se houver `listing_id`: chip clicável "Ver anúncio pausado MLB123..." que abre em nova aba
4. Botão "Fechar" aparece abaixo das etapas — ao clicar, painel fecha e botão retorna
5. `showToast(mensagem_erro, "error", 8000)`

### Sem cancelamento durante execução

Enquanto o job está em andamento, não há botão de fechar nem de cancelar. O painel fica aberto até conclusão (sucesso ou erro).

### Persistência pós-F5

- Ao iniciar publicação: salvar `{ job_id, started_at, sku, listing_type_id }` em `localStorage` (chave: `ml_active_job`)
- No boot da página (`DOMContentLoaded`): verificar `localStorage.ml_active_job`
  - Se existir e `Date.now() - started_at < 10 * 60 * 1000`: reabrir painel em modo "Reconectando..." e reconectar `EventSource`
  - Se expirado (> 10 min) ou HTTP 410: limpar `localStorage`, não reabrir painel
- Limpar `localStorage.ml_active_job` ao receber evento `done` ou `error`

---

## Seção 3: Configurações ML dentro de "Integrações"

Adicionado como novo sub-bloco após o bloco "Canva Integration" existente, seguindo o mesmo padrão visual: `bg-gray-50 p-4 rounded-lg border border-gray-200`.

### Cabeçalho da sub-seção

```html
<div class="pt-2 border-t border-gray-100 mt-4">
  <h5 class="text-sm font-semibold text-gray-800 mb-4 flex items-center">
    <!-- ícone ML ou shopping bag -->
    Mercado Livre
  </h5>
  <div class="bg-gray-50 p-4 rounded-lg border border-gray-200">
    <h6 class="text-xs font-bold text-gray-600 uppercase tracking-wider mb-3">
      Mercado Livre API (OAuth)
    </h6>
    ...
  </div>
</div>
```

### Sub-bloco: Contas conectadas

```
Contas ML conectadas                    [+ Conectar conta]
```

- "Conectar conta" chama `GET /api/ml/auth` (redirect OAuth ML)
- Cada conta renderizada como card expansível:

```
┌──────────────────────────────────────────────────────┐
│  [badge verde] MINHA_LOJA_ML (ID: 1452969010)   [▼] │
│  Conectada em 05/03/2026            [Desconectar]    │
│                                                      │
│  Mapeamento de Categorias  [+ Adicionar categoria]   │
│  ┌──────────────────┬──────────────────┬──────────┐  │
│  │ Categoria AdsGen │ Categoria ML     │  Ações   │  │
│  ├──────────────────┼──────────────────┼──────────┤  │
│  │ tapete higiênico │ Tapetes Higien.  │ [Remove] │  │
│  │                  │ MLB178930 (muted)│          │  │
│  └──────────────────┴──────────────────┴──────────┘  │
└──────────────────────────────────────────────────────┘
```

Detalhes:
- `[▼]` expande/colapsa a tabela DE/PARA da conta (colapsada por padrão se já preenchida)
- Badge de status: verde "Conectada" ou laranja "Token expirando" (< 30 min para expirar)
- "Desconectar": confirmação inline no próprio card (sem modal extra), chama `DELETE /api/ml/accounts/{ml_user_id}`
- Tabela DE/PARA usa o mesmo padrão `overflow-x-auto + table w-full text-xs border border-gray-200` das demais tabelas do sistema
- Coluna "Categoria ML": nome legível + `category_id` em `text-xs text-gray-400` abaixo
- "Adicionar categoria": abre o `.modal` existente com campo de busca por texto na API ML (`/sites/MLB/domain_discovery/search?q=...`), lista resultados, seleciona e salva

### Fluxo de autenticação OAuth (nova conta)

Segue o mesmo padrão do Canva (`window.open` + `postMessage` + `window.close`):

1. Usuário clica "+ Conectar conta"
2. `window.open("/api/ml/auth", "_blank")` — abre nova aba com a página de autorização do ML
3. Usuário autoriza o App ML na conta dele
4. Backend (`GET /api/ml/callback`) recebe o `code`, troca pelo token, salva no banco e renderiza uma página mínima de callback que:
   - Executa `window.opener.postMessage({ type: "ml_oauth_result", status: "success", account: { ml_user_id, nickname } }, origin)`
   - Chama `window.close()` para fechar a aba automaticamente
5. A aba principal ouve via `setupMlOAuthPopupListener()` (análogo ao `setupCanvaOAuthPopupListener`)
6. Em caso de sucesso: recarrega a lista de contas ML na seção de Integrações **sem reload da página** + `showToast("Conta ML conectada com sucesso.", "success")`
7. Em caso de erro no callback: `postMessage({ type: "ml_oauth_result", status: "error", message: "..." })` → `showToast(msg, "error")`

Nota: **não há fallback via query string** (`?ml_auth=success`) como o Canva tem, pois a aba sempre fecha sozinha após o `postMessage`.

### Redirect URI

Exibir abaixo do botão "Conectar conta" o redirect URI configurado no App ML, seguindo o padrão do Canva:

```html
<p class="text-[10px] text-gray-500 mt-2">
  <strong>Redirect URI:</strong>
  <code id="mlRedirectUriDisplay">carregando...</code><br>
  (Copie e cole este endereço no console do App ML em "URLs de redirecionamento")
</p>
```

---

## Seção 4: Validação pré-publicação e Toasts

### Validação local (frontend)

Ao clicar "Publicar no ML", antes de qualquer chamada ao servidor, o JS valida os campos obrigatórios do workspace ativo:

| Campo | Elemento DOM |
|---|---|
| Título | `#outTitle` |
| Descrição | `#outDesc` |
| Preço do anúncio | `#tinyAnnouncePriceMin` (aba % Min) ou `#tinyAnnouncePriceMax` (aba % Max) |
| Custo base | `#tinyCostPrice` |
| Custo de frete | `#tinyShippingCost` |
| Peso | `#tinyWeight` |
| Altura | `#tinyHeight` |
| Largura | `#tinyWidth` |
| Comprimento | `#tinyLength` |
| Conta ML conectada | ao menos 1 entrada em `ml_accounts` |
| Categoria mapeada | DE/PARA da conta selecionada cobre a categoria do workspace |

Se falhar: `showToast("Campos obrigatórios não preenchidos: Peso, Frete, ...", "error", 8000)`. O painel não abre e o botão permanece habilitado.

### Ajuste do sistema de toasts

**Mudança de posição:** `right:1.5rem` → `left:1.5rem` no `ensureToastContainer()`.

Toasts ficam no canto **inferior esquerdo**, fora da área central de conteúdo e sem qualquer sobreposição com o painel ML (ancorado à direita).

Nenhuma outra mudança no mecanismo de `showToast` — a implementação existente já suporta todos os recursos necessários (variantes, progress bar, persistent, updateToast, auto-dismiss).

### Toasts do fluxo ML

| Momento | Chamada |
|---|---|
| Validação falhou | `showToast("Campos: X, Y, Z", "error", 8000)` |
| Publicação concluída | `showToast("Anúncio publicado! Ver anúncio ↗", "success", 5000)` com link |
| Erro em etapa SSE | `showToast(msg, "error", 8000)` |
| Token ML expirado | `showToast("Reconecte sua conta ML em Configurações.", "warning", 0, { persistent: true })` |

---

## Resumo dos novos elementos

| Elemento | Tipo | Onde |
|---|---|---|
| `.publish-rail` / `.publish-rail-inner` | CSS + HTML | `#resultCard` (espelho do variant-tabs-rail, lado direito) |
| `.publish-rail-btn` | Botão CSS branding ML | Dentro do `.publish-rail` |
| `.publish-panel` | Painel expansível CSS | Substitui o botão no mesmo rail |
| Sub-bloco "Mercado Livre" | HTML | Seção "Integrações" do painel de Configurações |
| Modal de busca de categoria | Reutiliza `.modal` existente | Disparado por "+ Adicionar categoria" |
| Toast position fix | JS (1 linha) | `ensureToastContainer()` — `right` → `left` |
| `localStorage` ml_active_job | JS | Boot da página + início/fim de publicação |
