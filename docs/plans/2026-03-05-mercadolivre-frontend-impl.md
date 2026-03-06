# Mercado Livre Frontend Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implementar a interface completa de publicação no Mercado Livre — botão flutuante com branding ML, painel de progresso SSE inline, seção de contas e categorias em Configurações > Integrações.

**Architecture:** Frontend-heavy (todo o HTML/CSS/JS vive em `static/main.html`). Backend já implementado; única mudança backend é o `ml_callback` que precisa retornar HTML com `postMessage + window.close()` em vez de redirect. Testes existentes (`pytest tests/`) cobrem o backend — rodar ao final para garantir que nada quebrou.

**Tech Stack:** Tailwind CSS (CDN), JS vanilla, FastAPI SSE (`EventSource`), localStorage para persistência de job.

---

## Contexto do Codebase

### O que já existe e NÃO deve ser tocado

- `mercadolivre_service.py` — cliente ML completo: auth, tokens, create_listing, upload_image, freight, activate, validate
- `app.py` — todos os endpoints ML implementados: `/api/ml/auth`, `/api/ml/callback`, `/api/ml/accounts`, `DELETE /api/ml/accounts/{id}`, `POST /api/ml/publish`, `GET /api/ml/publish/{job_id}/events`, `/api/ml/categories` (CRUD + search + auto-populate)
- `tests/test_mercadolivre_*.py` — 4 arquivos de teste existentes; devem continuar passando
- `config.py` — já tem `ml_client_id`, `ml_client_secret`, `whatsapp_*`

### O que precisa ser criado/modificado

| Arquivo | Tipo de mudança |
|---|---|
| `app.py` | Modificar apenas `ml_callback` (linha ~3628): trocar redirect por HTML postMessage |
| `static/main.html` | Adicionar CSS, HTML e JS para toda a UI ML |

### Padrões do projeto para seguir

- **CSS**: inline no `<style>` no `<head>` do `main.html` (antes de `</style>` na linha ~682)
- **HTML**: estrutura dentro do `<body>`, seguindo os padrões existentes de `.card`, `.btn`, `.modal`, `.badge`
- **JS**: inline no `<script>` no final do arquivo (após linha ~2025)
- **Toasts**: usar `showToast(message, variant, duration)` e `updateToast(id, {})` já existentes
- **Modal**: reutilizar `.modal` / `.modal-card` existentes (z-index 50, fundo escurecido)
- **Config sections**: padrão `cfg-section` com `data-section="integrations"`
- **Canva como referência**: `setupCanvaOAuthPopupListener()` e `btnAuthCanva` listener são o modelo exato para ML

### IDs e seletores importantes existentes

```
#resultCard          — o card de resultado (onde o rail ML será ancorado)
#marketplace         — <select> com valor "mercadolivre"
.price-tab-btn       — abas % Min / % Max (data-tab="min"|"max")
.variant-tabs-rail   — rail esquerdo de abas (espelhar para o direito)
#outTitle            — textarea do título gerado
#outDesc             — textarea da descrição gerada
#tinyCostPrice       — input do custo base
#tinyShippingCost    — input do custo de frete
#tinyWeight / #tinyHeight / #tinyWidth / #tinyLength — dimensões
#tinyAnnouncePriceMin / #tinyAnnouncePriceMax — preços por aba
.cfg-section[data-section="integrations"] — seção Integrações no config panel
ensureToastContainer() — cria/retorna o container de toasts (mudar right→left)
setupCanvaOAuthPopupListener() — modelo para setupMlOAuthPopupListener()
```

### Cores ML para referência

```
#FFE600  — amarelo ML (fundo do botão)
#F0D800  — amarelo ML escuro (hover, borda)
#2D73E2  — azul ML (texto, ícones)
#FFF9E0  — amarelo muito claro (fundo do painel)
```

---

## Task 1: Corrigir ml_callback para postMessage + window.close()

**Arquivo:** `app.py`

O callback atual faz `return RedirectResponse(url="/?ml_auth=success")`. Precisa retornar HTML que:
1. Faz `postMessage({ type: "ml_oauth_result", status: "success", account: {...} })` para `window.opener`
2. Chama `window.close()`
3. Em caso de erro: `postMessage({ type: "ml_oauth_result", status: "error", message: "..." })`

**Step 1: Localizar a linha atual**

No `app.py`, linha ~3628, encontre:
```python
    return RedirectResponse(url="/?ml_auth=success")
```

**Step 2: Substituir pelo retorno HTML**

Substitua o `return RedirectResponse(url="/?ml_auth=success")` ao final de `ml_callback` por:

```python
    account_safe = {
        "ml_user_id": account.get("ml_user_id"),
        "nickname": account.get("nickname"),
        "expires_at": account.get("expires_at"),
    }
    import json as _json
    payload_js = _json.dumps({"type": "ml_oauth_result", "status": "success", "account": account_safe})
    origin = str(request.base_url).rstrip("/")
    html = f"""<!DOCTYPE html><html><body><script>
    (function() {{
        var payload = {payload_js};
        var origin = "{origin}";
        try {{
            if (window.opener && !window.opener.closed) {{
                window.opener.postMessage(payload, origin);
                window.close();
                return;
            }}
        }} catch(e) {{}}
        window.location.href = "/?ml_auth=success";
    }})();
    </script></body></html>"""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html)
```

Também substituir o bloco de erro (quando `error` vem do ML, linha ~3575-3578):

```python
    if error:
        err_payload = _json.dumps({"type": "ml_oauth_result", "status": "error", "message": f"OAuth ML retornou erro: {error}"})
        origin = str(request.base_url).rstrip("/")
        html = f"""<!DOCTYPE html><html><body><script>
        (function() {{
            var payload = {err_payload};
            var origin = "{origin}";
            try {{
                if (window.opener && !window.opener.closed) {{
                    window.opener.postMessage(payload, origin);
                    window.close();
                    return;
                }}
            }} catch(e) {{}}
            window.location.href = "/";
        }})();
        </script></body></html>"""
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=html)
```

**Nota:** O `import json as _json` pode ser movido para o topo do arquivo se preferir — mas como é uma adição pontual, inline é aceitável para não tocar em mais código.

**Step 3: Rodar os testes para garantir que nada quebrou**

```bash
pytest tests/ -v
```

Esperado: todos os testes passando.

**Step 4: Commit**

```bash
git add app.py
git commit -m "fix(ml): callback returns postMessage HTML instead of redirect"
```

---

## Task 2: Fix toast position (right → left)

**Arquivo:** `static/main.html`

**Step 1: Localizar a linha**

Procure em `ensureToastContainer()` (linha ~4950):
```javascript
        'position:fixed', 'bottom:1.5rem', 'right:1.5rem',
```

**Step 2: Alterar right → left**

```javascript
        'position:fixed', 'bottom:1.5rem', 'left:1.5rem',
```

**Step 3: Verificar visualmente**

Abrir o sistema no browser, acionar qualquer toast (ex: pesquisar sem nome de produto) e verificar que aparece no canto inferior **esquerdo**.

**Step 4: Commit**

```bash
git add static/main.html
git commit -m "fix(ui): move toast container to bottom-left to avoid overlap with ML panel"
```

---

## Task 3: Adicionar CSS do publish-rail e publish-panel

**Arquivo:** `static/main.html` — dentro do bloco `<style>` (antes da linha `</style>` ~682)

**Step 1: Adicionar os estilos**

Inserir após o último bloco de CSS existente (após `.zoom-btn:hover { ... }`, antes de `</style>`):

```css
    /* ── Publish Rail (ML e futuros marketplaces) ── */
    .publish-rail {
      position: absolute;
      right: -54px;
      top: 0;
      bottom: 0;
      width: 0;
      overflow: visible;
      z-index: 8;
    }

    .publish-rail-inner {
      position: sticky;
      top: 84px;
      pointer-events: auto;
      display: flex;
      align-items: flex-start;
    }

    .publish-rail-btn {
      position: relative;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: auto;
      min-width: 2.1rem;
      min-height: 4.2rem;
      padding: 0.55rem 0.34rem;
      background: #FFE600;
      color: #2D73E2;
      border: 2px solid #F0D800;
      border-radius: 0.7rem;
      font-weight: 700;
      box-shadow: 2px 2px 10px rgba(45, 115, 226, 0.20);
      transition: background 0.18s ease, box-shadow 0.2s ease, opacity 0.18s ease;
      cursor: pointer;
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
      display: inline-flex;
      align-items: center;
      justify-content: center;
      writing-mode: vertical-rl;
      text-orientation: mixed;
      transform: rotate(180deg);
      white-space: nowrap;
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0.015em;
      line-height: 1;
    }

    /* ── Publish Panel ── */
    .publish-panel {
      width: 280px;
      background: #FFF9E0;
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

    .publish-panel-header {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.65rem 0.75rem;
      background: #FFE600;
      font-size: 0.82rem;
      font-weight: 700;
      color: #2D73E2;
    }

    .publish-panel-header.success {
      background: #d1fae5;
      color: #065f46;
    }

    .publish-panel-header.error {
      background: #fee2e2;
      color: #991b1b;
    }

    .publish-panel-context {
      padding: 0.5rem 0.75rem;
      font-size: 0.72rem;
      color: #4b5563;
      border-bottom: 1px solid #FFE600;
    }

    .publish-panel-steps {
      padding: 0.5rem 0.75rem;
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
    }

    .publish-step {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      font-size: 0.75rem;
    }

    .publish-step.done    { color: #10b981; }
    .publish-step.running { color: #2D73E2; }
    .publish-step.pending { color: #9ca3af; }
    .publish-step.failed  { color: #ef4444; }

    .publish-panel-footer {
      padding: 0.5rem 0.75rem;
      border-top: 1px solid rgba(255,230,0,0.4);
    }

    @media (max-width: 1024px) {
      .publish-rail { display: none; }
    }
```

**Step 2: Rodar os testes para garantir que nada quebrou**

```bash
pytest tests/ -v
```

**Step 3: Commit**

```bash
git add static/main.html
git commit -m "feat(ui): add CSS for ML publish rail and progress panel"
```

---

## Task 4: HTML — Publish Rail no #resultCard

**Arquivo:** `static/main.html`

O `#resultCard` já tem a estrutura `.result-layout` com `.variant-tabs-rail` à esquerda. Precisamos adicionar o `.publish-rail` à direita, dentro de `.result-layout`.

**Step 1: Localizar o ponto de inserção**

Encontre (linha ~962):
```html
      <div class="result-layout">
        <aside class="variant-tabs-rail">
```

**Step 2: Adicionar o publish-rail após o fechamento do `<div class="result-main">`**

Localize o fechamento da `.result-main` (antes do fechamento de `.result-layout`). Adicione logo após `</div> <!-- /result-main -->`:

```html
        <!-- Publish Rail — marketplace-specific publish button -->
        <aside class="publish-rail" id="publishRail" style="display:none" aria-label="Publicar no marketplace">
          <div class="publish-rail-inner">
            <!-- Estado: botão -->
            <button
              class="publish-rail-btn"
              id="btnPublishMl"
              data-marketplace="mercadolivre"
              title="Publicar no Mercado Livre"
              aria-label="Publicar no Mercado Livre">
              <span class="publish-rail-label">Publicar no ML</span>
            </button>
            <!-- Estado: painel (injetado via JS quando publicação inicia) -->
            <div class="publish-panel" id="publishPanel" style="display:none">
              <div class="publish-panel-header" id="publishPanelHeader">
                <div class="loader" id="publishPanelLoader" style="width:16px;height:16px;border-width:2px;border-top-color:#2D73E2;border-color:#FFE600"></div>
                <span id="publishPanelTitle">Publicando no ML...</span>
              </div>
              <div class="publish-panel-context" id="publishPanelContext"></div>
              <div class="publish-panel-steps" id="publishPanelSteps"></div>
              <div class="publish-panel-footer" id="publishPanelFooter" style="display:none"></div>
            </div>
          </div>
        </aside>
```

**Step 3: Verificar no browser**

O rail não deve aparecer ainda (JS ainda não feito). Verifique que não quebrou o layout existente.

**Step 4: Commit**

```bash
git add static/main.html
git commit -m "feat(ui): add ML publish rail HTML to result card"
```

---

## Task 5: HTML — Seção ML em Configurações > Integrações

**Arquivo:** `static/main.html`

A seção Integrações (`.cfg-section[data-section="integrations"]`) termina com o bloco Canva (linha ~1871). Adicionar o bloco ML após ele.

**Step 1: Localizar o fim do bloco Canva**

Encontre o fim do sub-bloco Canva Integration (linha ~1871):
```html
              </div>
            </div>
          </div>
```

**Step 2: Inserir o bloco ML após o fechamento do Canva**

Após o último `</div>` do bloco Canva e antes do `</div>` que fecha a `.cfg-section[data-section="integrations"]`:

```html
              <!-- Mercado Livre Integration Sub-group -->
              <div class="pt-2 border-t border-gray-100 mt-4">
                <h5 class="text-sm font-semibold text-gray-800 mb-4 flex items-center">
                  <svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                      d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13L5.4 5M7 13l-2.293 2.293c-.63.63-.184 1.707.707 1.707H17m0 0a2 2 0 100 4 2 2 0 000-4zm-8 2a2 2 0 11-4 0 2 2 0 014 0z">
                    </path>
                  </svg>
                  Mercado Livre
                </h5>

                <div class="bg-gray-50 p-4 rounded-lg border border-gray-200">
                  <h6 class="text-xs font-bold text-gray-600 uppercase tracking-wider mb-3">Mercado Livre API (OAuth)</h6>

                  <div class="flex items-center justify-between mb-3">
                    <p class="text-xs text-gray-500">Conecte contas do Mercado Livre para publicar anúncios diretamente.</p>
                    <button id="btnConnectMl"
                      class="px-3 py-1.5 bg-yellow-400 text-blue-700 border border-yellow-500 rounded text-xs font-bold hover:bg-yellow-300 transition-colors flex items-center gap-1">
                      + Conectar conta
                    </button>
                  </div>

                  <p class="text-[10px] text-gray-500 mb-3">
                    <strong>Redirect URI:</strong>
                    <code class="bg-gray-200 px-1 rounded" id="mlRedirectUriDisplay">carregando...</code><br>
                    (Copie e cole este endereço no console do App ML em "URLs de redirecionamento")
                  </p>

                  <div id="mlAccountsList" class="space-y-2">
                    <p class="text-xs text-gray-500 italic">Nenhuma conta ML conectada</p>
                  </div>
                </div>
              </div>
```

**Step 3: Adicionar modal de busca de categoria ML**

Adicionar antes de `</body>` (após os outros modais existentes):

```html
  <!-- Modal: Busca de Categoria ML -->
  <div id="mlCategorySearchModal" class="modal" role="dialog" aria-modal="true" aria-labelledby="mlCatModalTitle">
    <div class="modal-card card p-6">
      <div class="flex items-center justify-between mb-4">
        <h3 class="text-lg font-semibold text-gray-800" id="mlCatModalTitle">Adicionar Categoria ML</h3>
        <button id="mlCatModalClose" class="btn" title="Fechar">Fechar</button>
      </div>
      <div class="mb-3">
        <label class="text-sm text-gray-700 font-medium">Pesquisar na árvore do Mercado Livre</label>
        <div class="flex gap-2 mt-1">
          <input id="mlCatSearchInput" type="text"
            class="flex-1 rounded-md border border-gray-300 bg-white px-3 py-2 text-sm"
            placeholder="Ex.: tapete higiênico, cama para cachorro...">
          <button id="btnMlCatSearch"
            class="px-3 py-1.5 bg-indigo-600 text-white rounded text-sm font-medium hover:bg-indigo-700">Buscar</button>
        </div>
      </div>
      <div class="mb-3">
        <label class="text-sm text-gray-700 font-medium">Nome no Ads Gen (DE)</label>
        <input id="mlCatAdsgenName" type="text"
          class="mt-1 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm"
          placeholder="Ex.: tapete higiênico">
      </div>
      <div id="mlCatSearchResults" class="max-h-48 overflow-y-auto border border-gray-200 rounded-md divide-y divide-gray-100 mb-3" style="display:none"></div>
      <div id="mlCatSelectedInfo" class="text-xs text-gray-600 mb-3" style="display:none"></div>
      <div class="flex justify-end gap-2">
        <button id="mlCatModalCancel" class="btn">Cancelar</button>
        <button id="btnMlCatSave" disabled
          class="px-3 py-1.5 rounded-lg text-sm font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed">Salvar</button>
      </div>
    </div>
  </div>
```

**Step 4: Commit**

```bash
git add static/main.html
git commit -m "feat(ui): add ML integrations config section and category search modal HTML"
```

---

## Task 6: JS — Publish Rail visibility + listing_type_id

**Arquivo:** `static/main.html` — bloco `<script>` no final

Adicionar no bloco JS, próximo à inicialização dos event listeners do marketplace select:

**Step 1: Função de visibilidade do rail**

```javascript
  // ── ML Publish Rail ──────────────────────────────────────────────────────
  const publishRail = document.getElementById('publishRail');
  const btnPublishMl = document.getElementById('btnPublishMl');
  const publishPanel = document.getElementById('publishPanel');

  function updatePublishRailVisibility() {
    const mp = document.getElementById('marketplace');
    if (!mp) return;
    publishRail.style.display = mp.value === 'mercadolivre' ? '' : 'none';
  }

  document.getElementById('marketplace').addEventListener('change', updatePublishRailVisibility);
  updatePublishRailVisibility(); // estado inicial
```

**Step 2: Função para ler listing_type_id ativo**

```javascript
  function getActiveListingTypeId() {
    const activeTab = document.querySelector('.price-tab-btn.active');
    if (!activeTab) return 'gold_special';
    return activeTab.dataset.tab === 'max' ? 'gold_pro' : 'gold_special';
  }
```

**Step 3: Commit**

```bash
git add static/main.html
git commit -m "feat(ui): add publish rail visibility logic and listing_type_id detection"
```

---

## Task 7: JS — ML OAuth popup listener + renderizar contas

**Arquivo:** `static/main.html` — bloco `<script>`

**Step 1: Variável de estado das contas ML (em memória, carregada da config)**

```javascript
  let mlAccounts = []; // populado por loadConfigFromServer via appConfig.ml_accounts
```

**Step 2: Função para renderizar a lista de contas**

```javascript
  function renderMlAccounts() {
    const container = document.getElementById('mlAccountsList');
    if (!container) return;
    if (!mlAccounts || mlAccounts.length === 0) {
      container.innerHTML = '<p class="text-xs text-gray-500 italic">Nenhuma conta ML conectada</p>';
      return;
    }
    container.innerHTML = mlAccounts.map(acc => {
      const expAt = acc.expires_at ? acc.expires_at * 1000 : null;
      const now = Date.now();
      const expiringMin = 30 * 60 * 1000;
      const isExpiring = expAt && (expAt - now) < expiringMin;
      const badgeClass = isExpiring ? 'bg-orange-100 text-orange-700 border-orange-200' : 'bg-green-100 text-green-700 border-green-200';
      const badgeLabel = isExpiring ? 'Token expirando' : 'Conectada';
      const connectedDate = acc.expires_at
        ? new Date((acc.expires_at - 6 * 3600) * 1000).toLocaleDateString('pt-BR')
        : '—';
      const cats = (appConfig.ml_category_mappings || []).filter(c => c.ml_user_id === acc.ml_user_id);
      const catsHtml = cats.length > 0
        ? cats.map(c => `
          <tr>
            <td class="px-2 py-1">${c.adsgen_name}</td>
            <td class="px-2 py-1">${c.ml_category_name || ''}<br><span class="text-gray-400 text-[10px]">${c.ml_category_id}</span></td>
            <td class="px-2 py-1 text-center">
              <button class="text-xs text-red-500 hover:text-red-700 btn-ml-remove-cat" data-adsgen="${c.adsgen_name}">Remover</button>
            </td>
          </tr>`).join('')
        : `<tr><td colspan="3" class="px-2 py-2 text-center text-gray-400 italic text-xs">Nenhuma categoria mapeada</td></tr>`;

      return `
        <div class="border border-gray-200 rounded-lg overflow-hidden" data-ml-user-id="${acc.ml_user_id}">
          <div class="flex items-center justify-between px-3 py-2 bg-white">
            <div class="flex items-center gap-2">
              <span class="badge ${badgeClass} text-[10px]">${badgeLabel}</span>
              <span class="text-sm font-medium text-gray-800">${acc.nickname || acc.ml_user_id}</span>
              <span class="text-xs text-gray-400">(ID: ${acc.ml_user_id})</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-xs text-gray-400">Conectada em ${connectedDate}</span>
              <button class="btn text-xs text-red-600 border-red-200 hover:bg-red-50 btn-ml-disconnect" data-ml-user-id="${acc.ml_user_id}">Desconectar</button>
              <button class="btn text-xs btn-ml-toggle-cats" data-ml-user-id="${acc.ml_user_id}" title="Expandir/colapsar categorias">▼</button>
            </div>
          </div>
          <div class="ml-cats-panel px-3 pb-3 bg-gray-50 border-t border-gray-100" data-ml-user-id="${acc.ml_user_id}" style="display:none">
            <div class="flex items-center justify-between mt-2 mb-1">
              <span class="text-xs font-semibold text-gray-600">Mapeamento de Categorias</span>
              <button class="btn text-xs btn-ml-add-cat" data-ml-user-id="${acc.ml_user_id}">+ Adicionar categoria</button>
            </div>
            <div class="overflow-x-auto">
              <table class="w-full text-xs border border-gray-200 rounded-md">
                <thead class="bg-gray-100"><tr>
                  <th class="px-2 py-1 text-left font-medium text-gray-700">Categoria Ads Gen</th>
                  <th class="px-2 py-1 text-left font-medium text-gray-700">Categoria ML</th>
                  <th class="px-2 py-1 text-center font-medium text-gray-700 w-20">Ações</th>
                </tr></thead>
                <tbody>${catsHtml}</tbody>
              </table>
            </div>
          </div>
        </div>`;
    }).join('');

    // Bind events após renderizar
    container.querySelectorAll('.btn-ml-disconnect').forEach(btn => {
      btn.addEventListener('click', () => mlDisconnectAccount(btn.dataset.mlUserId));
    });
    container.querySelectorAll('.btn-ml-toggle-cats').forEach(btn => {
      btn.addEventListener('click', () => {
        const panel = container.querySelector(`.ml-cats-panel[data-ml-user-id="${btn.dataset.mlUserId}"]`);
        if (panel) panel.style.display = panel.style.display === 'none' ? '' : 'none';
      });
    });
    container.querySelectorAll('.btn-ml-add-cat').forEach(btn => {
      btn.addEventListener('click', () => mlOpenCategoryModal(btn.dataset.mlUserId));
    });
    container.querySelectorAll('.btn-ml-remove-cat').forEach(btn => {
      btn.addEventListener('click', () => mlRemoveCategory(btn.dataset.adsgen));
    });
  }
```

**Step 3: Função de desconexão**

```javascript
  async function mlDisconnectAccount(mlUserId) {
    if (!confirm(`Desconectar conta ML ${mlUserId}?`)) return;
    try {
      const res = await fetch(`/api/ml/accounts/${mlUserId}`, { method: 'DELETE' });
      if (!res.ok) throw new Error((await res.json()).detail || 'Erro ao desconectar');
      mlAccounts = mlAccounts.filter(a => String(a.ml_user_id) !== String(mlUserId));
      renderMlAccounts();
      showToast('Conta ML desconectada.', 'info');
    } catch (err) {
      showToast(`Erro: ${err.message}`, 'error');
    }
  }
```

**Step 4: setupMlOAuthPopupListener (modelo: setupCanvaOAuthPopupListener)**

```javascript
  function setupMlOAuthPopupListener() {
    window.addEventListener('message', async (event) => {
      if (event.origin !== window.location.origin) return;
      const data = event.data;
      if (!data || typeof data !== 'object' || data.type !== 'ml_oauth_result') return;
      if (data.status === 'success') {
        showToast('Conta ML conectada com sucesso!', 'success');
        // Recarregar config para pegar a nova conta
        const res = await fetch('/api/ml/accounts');
        if (res.ok) {
          const body = await res.json();
          mlAccounts = body.accounts || [];
          renderMlAccounts();
        }
      } else {
        showToast(String(data.message || 'Falha ao conectar conta ML.'), 'error');
      }
    });
  }
```

**Step 5: Botão "Conectar conta" e redirect URI**

```javascript
  const btnConnectMl = document.getElementById('btnConnectMl');
  if (btnConnectMl) {
    btnConnectMl.addEventListener('click', () => {
      window.open('/api/ml/auth', '_blank');
    });
  }

  // Preencher redirect URI display
  const mlRedirectUriDisplay = document.getElementById('mlRedirectUriDisplay');
  if (mlRedirectUriDisplay) {
    mlRedirectUriDisplay.textContent = `${window.location.origin}/api/ml/callback`;
  }
```

**Step 6: Integrar com loadConfigFromServer existente**

No `loadConfigFromServer` (ou onde `appConfig` é populado após `GET /api/config`), adicionar:

```javascript
  // Após appConfig ser preenchido:
  mlAccounts = Array.isArray(appConfig.ml_accounts) ? appConfig.ml_accounts : [];
  renderMlAccounts();
```

E no boot (próximo a `setupCanvaOAuthPopupListener()`):

```javascript
  setupMlOAuthPopupListener();
```

**Step 7: Commit**

```bash
git add static/main.html
git commit -m "feat(ui): add ML accounts rendering, OAuth popup listener, disconnect"
```

---

## Task 8: JS — Category mapping modal

**Arquivo:** `static/main.html`

**Step 1: Variável de estado do modal**

```javascript
  let mlCatModalUserId = null;
  let mlCatSelectedCategory = null; // { id, name }
```

**Step 2: Função para abrir o modal**

```javascript
  function mlOpenCategoryModal(mlUserId) {
    mlCatModalUserId = mlUserId;
    mlCatSelectedCategory = null;
    document.getElementById('mlCatSearchInput').value = '';
    document.getElementById('mlCatAdsgenName').value = '';
    document.getElementById('mlCatSearchResults').style.display = 'none';
    document.getElementById('mlCatSearchResults').innerHTML = '';
    document.getElementById('mlCatSelectedInfo').style.display = 'none';
    document.getElementById('btnMlCatSave').disabled = true;
    document.getElementById('mlCategorySearchModal').classList.add('open');
  }

  function mlCloseCategoryModal() {
    document.getElementById('mlCategorySearchModal').classList.remove('open');
    mlCatModalUserId = null;
    mlCatSelectedCategory = null;
  }
```

**Step 3: Busca de categorias**

```javascript
  document.getElementById('btnMlCatSearch').addEventListener('click', async () => {
    const q = document.getElementById('mlCatSearchInput').value.trim();
    if (!q) return;
    const resultsEl = document.getElementById('mlCatSearchResults');
    resultsEl.innerHTML = '<div class="p-2 text-xs text-gray-500">Buscando...</div>';
    resultsEl.style.display = '';
    try {
      const res = await fetch(`/api/ml/categories/search?q=${encodeURIComponent(q)}`);
      const body = await res.json();
      const items = body.categories || [];
      if (items.length === 0) {
        resultsEl.innerHTML = '<div class="p-2 text-xs text-gray-500 italic">Nenhuma categoria encontrada.</div>';
        return;
      }
      resultsEl.innerHTML = items.map(c =>
        `<button class="w-full text-left px-3 py-2 text-xs hover:bg-indigo-50 ml-cat-result-btn" data-id="${c.id}" data-name="${c.name}">
          <span class="font-medium">${c.name}</span>
          <span class="text-gray-400 ml-1">${c.id}</span>
        </button>`
      ).join('');
      resultsEl.querySelectorAll('.ml-cat-result-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          mlCatSelectedCategory = { id: btn.dataset.id, name: btn.dataset.name };
          const info = document.getElementById('mlCatSelectedInfo');
          info.textContent = `Selecionado: ${mlCatSelectedCategory.name} (${mlCatSelectedCategory.id})`;
          info.style.display = '';
          document.getElementById('btnMlCatSave').disabled = false;
        });
      });
    } catch (err) {
      resultsEl.innerHTML = `<div class="p-2 text-xs text-red-500">Erro: ${err.message}</div>`;
    }
  });
```

**Step 4: Salvar categoria**

```javascript
  document.getElementById('btnMlCatSave').addEventListener('click', async () => {
    const adsgenName = document.getElementById('mlCatAdsgenName').value.trim();
    if (!adsgenName || !mlCatSelectedCategory) {
      showToast('Preencha o nome no Ads Gen e selecione uma categoria ML.', 'warning');
      return;
    }
    try {
      const res = await fetch('/api/ml/categories', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ml_user_id: mlCatModalUserId,
          adsgen_name: adsgenName,
          ml_category_id: mlCatSelectedCategory.id,
          ml_category_name: mlCatSelectedCategory.name,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || 'Erro ao salvar');
      showToast('Categoria adicionada.', 'success');
      // Recarregar appConfig para refletir novo mapeamento
      await loadConfigFromServer();
      mlCloseCategoryModal();
    } catch (err) {
      showToast(`Erro: ${err.message}`, 'error');
    }
  });

  async function mlRemoveCategory(adsgenName) {
    try {
      const res = await fetch(`/api/ml/categories/${encodeURIComponent(adsgenName)}`, { method: 'DELETE' });
      if (!res.ok) throw new Error((await res.json()).detail || 'Erro ao remover');
      showToast('Categoria removida.', 'info');
      await loadConfigFromServer();
    } catch (err) {
      showToast(`Erro: ${err.message}`, 'error');
    }
  }
```

**Step 5: Bind de fechar modal**

```javascript
  document.getElementById('mlCatModalClose').addEventListener('click', mlCloseCategoryModal);
  document.getElementById('mlCatModalCancel').addEventListener('click', mlCloseCategoryModal);
```

**Step 6: Commit**

```bash
git add static/main.html
git commit -m "feat(ui): add ML category mapping modal with search and save"
```

---

## Task 9: JS — Publish button: validação + POST + SSE connect

**Arquivo:** `static/main.html`

### Mapeamento de steps SSE → labels de UI

```javascript
  const ML_STEP_LABELS = {
    token_refresh:      'Verificando credenciais ML...',
    downloading_images: 'Baixando imagens do Drive...',
    uploading_images:   'Enviando imagens ao Mercado Livre...',
    creating_listing:   'Criando anúncio no ML...',
    checking_freight:   'Consultando custo de frete...',
    adjusting_price:    'Frete divergente — recalculando preços...',
    updating_listing:   'Atualizando preço no anúncio...',
    notifying_whatsapp: 'Notificando divergência via WhatsApp...',
    activating:         'Ativando anúncio...',
    done:               'Anúncio publicado com sucesso!',
    error:              'Falha na publicação',
  };

  const ML_STEP_ORDER = [
    'token_refresh', 'downloading_images', 'uploading_images',
    'creating_listing', 'checking_freight',
    'adjusting_price', 'updating_listing', 'notifying_whatsapp',
    'activating', 'done',
  ];
```

### Validação local dos campos

```javascript
  function validateWorkspaceForMlPublish() {
    const missing = [];
    const activeTabBtn = document.querySelector('.price-tab-btn.active');
    const isMax = activeTabBtn && activeTabBtn.dataset.tab === 'max';
    const priceEl = document.getElementById(isMax ? 'tinyAnnouncePriceMax' : 'tinyAnnouncePriceMin');

    if (!document.getElementById('outTitle').value.trim()) missing.push('Título');
    if (!document.getElementById('outDesc').value.trim()) missing.push('Descrição');
    if (!priceEl || !parseFloat(priceEl.value)) missing.push('Preço do anúncio');
    if (!parseFloat(document.getElementById('tinyCostPrice').value)) missing.push('Custo base');
    if (!parseFloat(document.getElementById('tinyShippingCost').value)) missing.push('Custo de frete');
    if (!parseFloat(document.getElementById('tinyWeight').value)) missing.push('Peso');
    if (!parseFloat(document.getElementById('tinyHeight').value)) missing.push('Altura');
    if (!parseFloat(document.getElementById('tinyWidth').value)) missing.push('Largura');
    if (!parseFloat(document.getElementById('tinyLength').value)) missing.push('Comprimento');
    if (!mlAccounts || mlAccounts.length === 0) missing.push('Conta ML (conecte em Configurações)');
    return missing;
  }
```

### Estado do painel e job ativo

```javascript
  let mlActiveEventSource = null;
  let mlPanelStepStates = {}; // { step: 'done'|'running'|'pending'|'failed' }

  function mlShowPanel() {
    btnPublishMl.style.display = 'none';
    publishPanel.style.display = '';
  }

  function mlHidePanel() {
    publishPanel.style.display = 'none';
    btnPublishMl.style.display = '';
  }

  function mlSetPanelContext(sku, listingTypeLabel) {
    const ctx = document.getElementById('publishPanelContext');
    ctx.textContent = `SKU: ${sku} · Tipo: ${listingTypeLabel}`;
  }

  function mlRenderSteps(currentStep, failedStep) {
    const stepsEl = document.getElementById('publishPanelSteps');
    const reachedCurrent = { reached: false };
    stepsEl.innerHTML = ML_STEP_ORDER
      .filter(s => s !== 'done')
      .map(step => {
        let state = 'pending';
        if (mlPanelStepStates[step]) {
          state = mlPanelStepStates[step];
        } else if (step === currentStep) {
          state = failedStep ? 'failed' : 'running';
          reachedCurrent.reached = true;
        }
        const icons = {
          done:    '<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"/></svg>',
          running: '<div class="loader shrink-0" style="width:12px;height:12px;border-width:2px;border-top-color:#2D73E2;border-color:#bfdbfe"></div>',
          pending: '<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" stroke-width="1.5"/></svg>',
          failed:  '<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>',
        };
        return `<div class="publish-step ${state}">${icons[state]}<span>${ML_STEP_LABELS[step] || step}</span></div>`;
      }).join('');
  }
```

### Click handler do botão publicar

```javascript
  btnPublishMl.addEventListener('click', async () => {
    const missing = validateWorkspaceForMlPublish();
    if (missing.length > 0) {
      showToast(`Campos obrigatórios não preenchidos: ${missing.join(', ')}`, 'error', 8000);
      return;
    }

    const listingTypeId = getActiveListingTypeId();
    const listingTypeLabel = listingTypeId === 'gold_pro' ? 'Anúncio Premium' : 'Anúncio Clássico';
    const sku = document.getElementById('tinySKUDisplay')?.value || document.getElementById('tinySKU')?.value || '';
    const mlUserId = mlAccounts[0]?.ml_user_id; // primeira conta conectada
    const variant = document.querySelector('.variant-tab-btn.active')?.dataset?.variant || 'simple';

    btnPublishMl.disabled = true;

    try {
      const res = await fetch('/api/ml/publish', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ml_user_id: mlUserId,
          sku,
          variant,
          listing_type_id: listingTypeId,
        }),
      });
      const body = await res.json();
      if (!res.ok) {
        showToast(body.detail || 'Erro ao iniciar publicação.', 'error', 8000);
        btnPublishMl.disabled = false;
        return;
      }
      const jobId = body.job_id;
      // Salvar no localStorage para persistência pós-F5
      localStorage.setItem('ml_active_job', JSON.stringify({
        job_id: jobId,
        started_at: Date.now(),
        sku,
        listing_type_id: listingTypeId,
      }));
      mlPanelStepStates = {};
      mlSetPanelContext(sku, listingTypeLabel);
      mlShowPanel();
      mlConnectSSE(jobId);
    } catch (err) {
      showToast(`Erro: ${err.message}`, 'error', 8000);
      btnPublishMl.disabled = false;
    }
  });
```

**Step commit:**

```bash
git add static/main.html
git commit -m "feat(ui): add ML publish button validation, POST and panel open logic"
```

---

## Task 10: JS — SSE event handling + estados do painel

**Arquivo:** `static/main.html`

```javascript
  function mlConnectSSE(jobId) {
    if (mlActiveEventSource) mlActiveEventSource.close();
    mlActiveEventSource = new EventSource(`/api/ml/publish/${jobId}/events`);

    mlActiveEventSource.onmessage = (evt) => {
      let data;
      try { data = JSON.parse(evt.data); } catch { return; }
      const { step, message, listing_id, listing_url, failed_at } = data;

      if (step === 'done') {
        mlPanelStepStates = Object.fromEntries(ML_STEP_ORDER.filter(s => s !== 'done').map(s => [s, 'done']));
        mlRenderSteps('done', false);
        // Atualizar header
        const header = document.getElementById('publishPanelHeader');
        header.className = 'publish-panel-header success';
        document.getElementById('publishPanelLoader').style.display = 'none';
        document.getElementById('publishPanelTitle').textContent = 'Anúncio publicado!';
        // Footer com link
        const footer = document.getElementById('publishPanelFooter');
        footer.style.display = '';
        footer.innerHTML = listing_url
          ? `<a href="${listing_url}" target="_blank" rel="noopener" class="text-xs text-blue-600 underline">Ver anúncio no ML ↗</a>`
          : '';
        // Toast + fechar painel após 2s
        showToast('Anúncio publicado no ML!', 'success', 5000);
        mlActiveEventSource.close();
        mlActiveEventSource = null;
        localStorage.removeItem('ml_active_job');
        setTimeout(() => { mlHidePanel(); btnPublishMl.disabled = false; }, 2000);
        return;
      }

      if (step === 'error') {
        const failStep = failed_at || 'desconhecido';
        if (mlPanelStepStates[failStep] !== 'done') {
          mlPanelStepStates[failStep] = 'failed';
        }
        mlRenderSteps(failStep, true);
        const header = document.getElementById('publishPanelHeader');
        header.className = 'publish-panel-header error';
        document.getElementById('publishPanelLoader').style.display = 'none';
        document.getElementById('publishPanelTitle').textContent = 'Falha na publicação';
        const footer = document.getElementById('publishPanelFooter');
        footer.style.display = '';
        footer.innerHTML = `<p class="text-xs text-red-600 mb-2">${message || 'Erro desconhecido'}</p>` +
          (listing_id ? `<a href="https://www.mercadolivre.com.br/anuncios/${listing_id}" target="_blank" rel="noopener" class="text-xs text-blue-600 underline">Ver anúncio pausado ${listing_id} ↗</a><br>` : '') +
          `<button id="btnMlPanelClose" class="mt-2 btn text-xs w-full">Fechar</button>`;
        footer.querySelector('#btnMlPanelClose')?.addEventListener('click', () => {
          mlHidePanel();
          btnPublishMl.disabled = false;
        });
        showToast(message || 'Falha ao publicar no ML.', 'error', 8000);
        mlActiveEventSource.close();
        mlActiveEventSource = null;
        localStorage.removeItem('ml_active_job');
        return;
      }

      // Etapa normal em andamento
      if (mlPanelStepStates[step] !== 'done') {
        // Marcar etapa anterior como done
        const idx = ML_STEP_ORDER.indexOf(step);
        if (idx > 0) {
          const prev = ML_STEP_ORDER[idx - 1];
          if (mlPanelStepStates[prev] !== 'failed') mlPanelStepStates[prev] = 'done';
        }
      }
      mlRenderSteps(step, false);
    };

    mlActiveEventSource.onerror = () => {
      // SSE dropped — pode ser reconexão automática do browser
      // Não fechar: EventSource tenta reconectar automaticamente
    };
  }
```

**Commit:**

```bash
git add static/main.html
git commit -m "feat(ui): add ML SSE event handling and panel state machine"
```

---

## Task 11: JS — localStorage persistence + boot reconnect

**Arquivo:** `static/main.html`

Adicionar próximo ao boot (`DOMContentLoaded` ou próximo a `setupCanvaOAuthPopupListener()`):

```javascript
  function mlCheckActiveJobOnBoot() {
    const raw = localStorage.getItem('ml_active_job');
    if (!raw) return;
    let saved;
    try { saved = JSON.parse(raw); } catch { localStorage.removeItem('ml_active_job'); return; }
    const { job_id, started_at, sku, listing_type_id } = saved;
    const TEN_MIN = 10 * 60 * 1000;
    if (!job_id || (Date.now() - started_at) > TEN_MIN) {
      localStorage.removeItem('ml_active_job');
      return;
    }
    // Há um job ativo — reabrir painel e reconectar SSE
    const listingTypeLabel = listing_type_id === 'gold_pro' ? 'Anúncio Premium' : 'Anúncio Clássico';
    mlPanelStepStates = {};
    mlSetPanelContext(sku || '—', listingTypeLabel);
    document.getElementById('publishPanelTitle').textContent = 'Reconectando...';
    // Exibir o rail (garantir que marketplace está correto)
    publishRail.style.display = '';
    mlShowPanel();
    btnPublishMl.disabled = true;
    mlConnectSSE(job_id);
  }
```

E no boot:

```javascript
  setupMlOAuthPopupListener();
  mlCheckActiveJobOnBoot();
```

**Commit:**

```bash
git add static/main.html
git commit -m "feat(ui): add ML job persistence and boot reconnect via localStorage"
```

---

## Task 12: Verificação final — rodar todos os testes

```bash
pytest tests/ -v
```

Esperado: todos os testes passando, incluindo os 4 arquivos `test_mercadolivre_*.py`.

Se algum teste falhar após as mudanças em `app.py` (Task 1), investigar antes de continuar.

**Commit final (se houver ajustes):**

```bash
git add -p
git commit -m "fix: address test failures after ml_callback refactor"
```

---

## Checklist de verificação manual no browser

Após implementar todas as tasks:

- [ ] Toast aparece no canto inferior **esquerdo**
- [ ] Botão "Publicar no ML" aparece somente com marketplace = Mercado Livre selecionado
- [ ] Botão está à direita do result card, vertical, amarelo com texto azul
- [ ] Clicar sem campos preenchidos mostra toast de erro com lista de campos
- [ ] Com campos preenchidos: painel expande, loader aparece, etapas progridem conforme SSE
- [ ] Em estado de sucesso: header fica verde, link do anúncio aparece, painel fecha após 2s, toast de sucesso
- [ ] Em estado de erro: header fica vermelho, botão "Fechar" aparece, listing_id exibido se disponível
- [ ] F5 durante publicação: painel reabre com "Reconectando..." e retoma o stream
- [ ] Após 10 minutos: job expirado não reabre o painel
- [ ] Em Configurações > Integrações: bloco ML aparece após o Canva
- [ ] "+ Conectar conta" abre nova aba OAuth ML; ao autorizar, aba fecha e conta aparece na lista
- [ ] Redirect URI exibido corretamente
- [ ] Expandir conta: tabela DE/PARA exibida
- [ ] "Adicionar categoria": modal abre, busca retorna resultados, salvar persiste
- [ ] "Remover" categoria: remove da tabela
- [ ] "Desconectar" conta: confirmação inline, conta removida da lista
