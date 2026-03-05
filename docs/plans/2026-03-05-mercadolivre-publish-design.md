# Design — Publicação de Anúncios no Mercado Livre

**Data:** 2026-03-05
**Status:** Aprovado

---

## Visão Geral

Adicionar ao adsGenerator a capacidade de criar anúncios no Mercado Livre via API oficial, com verificação de divergência de frete, recálculo automático de preços, notificação via WhatsApp e feedback em tempo real para o usuário via Server-Sent Events (SSE).

---

## Arquitetura Geral

**Novos arquivos:**
- `mercadolivre_service.py` — cliente ML: OAuth2, CRUD de anúncios, upload de imagens, consulta de frete, renovação automática de token

**Arquivos modificados:**
- `app.py` — novos endpoints OAuth, publicação e SSE
- `config.py` — variáveis `ml_client_id` e `ml_client_secret` (configuração global do App ML)

**Sem novas dependências externas** — `httpx` já está no projeto; SSE usa `StreamingResponse` nativo do FastAPI.

---

## Seção 1: OAuth e Armazenamento de Credenciais

### Configuração global (`config.py`)

```
ML_CLIENT_ID=...
ML_CLIENT_SECRET=...
```

Cadastradas uma única vez no ambiente. Um único App ML atende todos os usuários do sistema.

### Endpoints OAuth

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/api/ml/auth` | Gera URL de autorização ML e redireciona o usuário |
| `GET` | `/api/ml/callback` | Recebe `code`, troca pelo token, salva no banco |
| `GET` | `/api/ml/accounts` | Lista contas ML conectadas do usuário |
| `DELETE` | `/api/ml/accounts/{ml_user_id}` | Desconecta uma conta ML |

### Armazenamento (`user_config.data["ml_accounts"]`)

Lista de contas por usuário do sistema, seguindo o padrão existente do Canva:

```json
[
  {
    "ml_user_id": "123456789",
    "nickname": "MINHA_LOJA_ML",
    "access_token": "...",
    "refresh_token": "...",
    "expires_at": 1741200000,
    "token_obtained_at": 1741113600
  }
]
```

### Renovação automática de token

Implementada em `mercadolivre_service.py`. Antes de qualquer chamada à API ML:
- Verifica `expires_at`; se dentro de 5 minutos do vencimento, executa refresh via `POST /oauth/token` com `grant_type=refresh_token`
- Salva os novos tokens no banco
- Access token válido por 6 horas; refresh token válido por 180 dias
- Re-autorização manual necessária apenas após revogação ou 180 dias de inatividade

---

## Seção 2: Campos e Configurações

### Campos hardcoded

| Campo | Valor fixo | Motivo |
|---|---|---|
| `condition` | `"new"` | Negócio trabalha somente com produtos novos |
| `available_quantity` | `1` | Estoque nasce com 1 até sincronismo Tiny→ML ser implementado |

### Campos já existentes no workspace (validar antes de publicar)

- Título, descrição (variants do workspace)
- Imagens (Canva / Google Drive)
- Preço, custo
- `weight_kg`, `length_cm`, `width_cm`, `height_cm`

### Campo novo no workspace

- `ml_attributes` — dicionário de atributos exigidos pela categoria ML (ex: `BRAND`, `MODEL`), populado automaticamente via API ML após a categoria ser resolvida pelo mapeamento DE/PARA

### Nova seção em Configurações — "Integrações > Mercado Livre"

**1. Tabela de categorias (DE/PARA)**

Mapeamento entre nome de categoria usado no Ads Gen e `category_id` do ML.

- Ao conectar uma conta ML, o sistema escaneia os anúncios existentes via API e pré-popula a tabela automaticamente com os mapeamentos encontrados
- Botão "Adicionar categoria" abre busca por texto na árvore ML (`/sites/MLB/domain_discovery/search?q=...`) para vincular categorias novas manualmente

**2. Tipo de anúncio (`listing_type_id`)**

Decisão adiada intencionalmente. Após a infra de integração estar funcional, um script de análise será rodado nos anúncios existentes da conta ML para entender os valores em uso. Somente então o default e a configurabilidade do campo serão definidos.

### Validação antes de publicar

Perspectiva do Ads Gen: **todos os campos do workspace devem estar preenchidos** antes de publicar — incluindo imagens. O Ads Gen não é apenas um publicador; é o publicador de anúncios de alta performance e exige dados completos.

- Frontend exibe toast **"Validando dados..."** enquanto a verificação roda
- Se algum campo faltar: toast de erro listando os campos faltantes pelo nome; publicação bloqueada sem criar job
- Imagens são tratadas como campo comum na mesma lista — sem verificação separada

---

## Seção 3: Fluxo de Publicação com SSE

### Endpoints

| Método | Endpoint | Descrição |
|---|---|---|
| `POST` | `/api/ml/publish` | Valida campos, inicia job em background, retorna `{"job_id": "..."}` |
| `GET` | `/api/ml/publish/{job_id}/events` | Stream SSE com eventos de progresso |

**Jobs armazenados em memória** (dict global no processo). Expiram após 10 minutos. Sem banco, sem Redis.

### Sequência de eventos SSE

```
[Frontend] clica "Publicar no ML"
    ↓
[Toast] "Validando dados..."
    ↓
POST /api/ml/publish
    → se falhar: 422 com lista de campos faltantes (toast erro, sem job)
    → se ok: {"job_id": "abc123"}
    ↓
[Frontend] abre modal "Publicando..." + conecta EventSource /api/ml/publish/abc123/events
    ↓
{"step": "token_refresh",      "message": "Verificando credenciais ML..."}
{"step": "creating_listing",   "message": "Criando anúncio pausado no ML..."}
{"step": "downloading_images", "message": "Baixando imagens do Google Drive... (N imagens)"}
{"step": "uploading_images",   "message": "Enviando imagens ao Mercado Livre..."}
{"step": "checking_freight",   "message": "Consultando custo de frete no ML..."}

── se frete ML ≤ frete Ads Gen ─────────────────────────────────────────────
{"step": "activating",         "message": "Frete ok — ativando anúncio..."}

── se frete ML > frete Ads Gen ─────────────────────────────────────────────
{"step": "adjusting_price",    "message": "Frete divergente (Ads Gen R$ X → ML R$ Y) — recalculando preços..."}
{"step": "updating_listing",   "message": "Atualizando preço no anúncio..."}
{"step": "notifying_whatsapp", "message": "Enviando notificação de divergência via WhatsApp..."}
{"step": "activating",         "message": "Ativando anúncio com preço ajustado..."}
─────────────────────────────────────────────────────────────────────────────

{"step": "done", "listing_id": "MLB123456789", "listing_url": "https://..."}

── se erro em qualquer etapa ────────────────────────────────────────────────
{"step": "error", "failed_at": "<step>", "message": "...", "listing_id": "MLB123..." }
```

---

## Seção 4: Tratamento de Erros

| Situação | Comportamento |
|---|---|
| Campo faltante no workspace | Bloqueio pré-fluxo, lista nomeada no toast, sem job criado |
| Token ML expirado / refresh falhou | Evento `error` com instrução para reconectar a conta |
| Imagem não encontrada no Drive | Evento `error` com nome da imagem faltante |
| Falha no upload de imagem ao ML | Evento `error` com índice da imagem; `listing_id` incluído |
| Falha na criação do anúncio (ML 4xx) | Evento `error` com mensagem da API ML |
| Falha na consulta de frete | Evento `error`; anúncio permanece pausado; `listing_id` incluído |
| Falha no WhatsApp | **Não bloqueia** — erro logado, fluxo continua para ativação |
| Falha na ativação | Evento `error`; anúncio permanece pausado; `listing_id` incluído |
| Job não encontrado | HTTP 404 imediato |
| Job expirado (> 10 min) | HTTP 410 Gone |

**Anúncios pausados criados com erro não são deletados automaticamente** — o `listing_id` é retornado no evento de erro para que o usuário possa agir manualmente. Limpeza automática pode ser considerada em iteração futura.

**Falha no WhatsApp não bloqueia ativação** — a notificação é informativa; o anúncio com preço ajustado já está correto.

---

## Seção 5: Testes Automatizados

Todos os testes usam mocks `httpx` — sem chamadas reais à API do ML. Compatíveis com `pytest tests/`.

| Arquivo | Cobertura |
|---|---|
| `tests/test_mercadolivre_oauth.py` | Troca de code por token, refresh automático, expiração |
| `tests/test_mercadolivre_publish.py` | Fluxo completo mockado: happy path + cada ramo de erro |
| `tests/test_mercadolivre_freight_comparison.py` | Comparação frete ML vs Ads Gen, recálculo de preço |
| `tests/test_mercadolivre_category_mapping.py` | DE/PARA de categorias, validação de campos obrigatórios |
