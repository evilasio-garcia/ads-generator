# Árvore de Categorias Mercado Livre — Design Aprovado

**Data:** 2026-03-12
**Status:** Aprovado para desenvolvimento

---

## Contexto

A busca de categorias do Mercado Livre via API (`/sites/MLB/domain_discovery/search`) é limitada (máx 8 resultados, semântica fraca, não retorna path completo). O objetivo é montar a árvore completa de categorias MLB em memória no startup da aplicação, permitindo busca fuzzy local, exibição de path completo e cache persistente no banco de dados.

## Requisitos

1. Carregar árvore completa de categorias MLB no startup (background)
2. Cache persistente no DB com expiração de 60 dias (invalidável manualmente via DB)
3. Busca fuzzy local na árvore (sem chamadas à API do ML)
4. Exibir path completo da categoria (ex: "Pet Shop > Aves e Acessórios > Ração")
5. Árvore global — mesma para todos, independente da conta ML
6. Migração automática de mapeamentos antigos (sem path) para novo formato
7. Enter aciona pesquisa no modal
8. Primeira busca retorna 20 resultados + botão "Mostrar todos"

---

## Arquitetura

```
Backend (Python)                          Frontend (JS)
┌─────────────────────────┐              ┌──────────────────────────┐
│ Startup (background)    │              │ Modal "Adicionar Cat."   │
│ • DB cache válido?      │              │   Enter → busca local    │
│   Sim → carrega memória │              │   Resultados com path    │
│   Não → API + salva DB  │              │   completo               │
│                         │              │   Botão "Mostrar todos"  │
│ Endpoints:              │              │                          │
│ • /tree/search?q=&limit │◄─────────────│ Tabela de mapeamentos    │
│ • /tree/status          │              │   "Pet Shop > Aves >     │
│ • /categories/migrate   │              │    Ração (MLB420009)"    │
└─────────────────────────┘              └──────────────────────────┘
```

---

## 1. Persistência — Cache no DB

**Tabela: `mercadolivre_category_tree_cache`**
**Classe: `MercadoLivreCategoryTreeCache`**

| Coluna | Tipo | Descrição |
|--------|------|-----------|
| `id` | Integer, PK | Auto-increment |
| `site_id` | String, unique | Sempre "MLB" |
| `tree_data` | JSONB | Árvore completa (flat dict) |
| `node_count` | Integer | Quantidade de nós, para monitoramento |
| `loaded_at` | DateTime | Quando foi carregada |
| `expires_at` | DateTime | `loaded_at + 60 dias` |

**Invalidação manual:** `UPDATE mercadolivre_category_tree_cache SET expires_at = NOW() WHERE site_id = 'MLB'`

---

## 2. Carregamento no Startup

```
startup
  │
  ▼
DB tem cache com expires_at > now()?
  │
  ├─ Sim → carrega tree_data do DB para memória (instantâneo)
  │
  └─ Não → tem token ML disponível no DB?
       │
       ├─ Sim → dispara carregamento da API em background
       │        (BFS com ThreadPoolExecutor, max_workers=8)
       │        Ao concluir: salva no DB + carrega em memória
       │
       └─ Não → não carrega (carregará sob demanda na primeira busca)
```

**Estimativas:**
- ~2500 chamadas a `/categories/{id}` (endpoint público, sem token)
- 8 workers paralelos × ~200ms por chamada ≈ ~60 segundos
- Deploy com cache válido no DB: carregamento instantâneo

---

## 3. Estrutura da Árvore em Memória

Dict flat indexado por `category_id`:

```python
{
    "MLB1234": {
        "id": "MLB1234",
        "name": "Ração",
        "path": "Pet Shop > Aves e Acessórios > Ração",
        "children": ["MLB5678", "MLB9012"],
        "leaf": True
    },
    ...
}
```

---

## 4. Busca Fuzzy

**Biblioteca:** `rapidfuzz` (rápida, sem dependência C pesada)

**Algoritmo:**
- Normaliza query (lowercase, remove acentos)
- Compara contra `name + " " + path` de cada nó usando `fuzz.WRatio`
- Threshold mínimo: score >= 50
- Ordenação: score decrescente
- Default: top 20 resultados
- "Mostrar todos": sem limite (`limit=0`)

**Endpoint:** `GET /api/ml/categories/tree/search?q=ração&limit=20`

**Retorno:**
```json
{
    "results": [
        {"id": "MLB420009", "name": "Ração", "path": "Pet Shop > Aves e Acessórios > Ração"},
        ...
    ],
    "total_found": 47,
    "showing": 20,
    "has_more": true
}
```

---

## 5. Frontend — Modal Atualizado

**Mudanças no modal `mlCategorySearchModal`:**
- Input: Enter dispara busca (keydown listener)
- Busca chama `GET /api/ml/categories/tree/search?q=...` (substitui endpoint antigo)
- Resultados mostram path completo: "Pet Shop > Aves e Acessórios > Ração (MLB420009)"
- Loading state enquanto árvore carrega pela primeira vez
- Botão "Mostrar todos os resultados" quando `has_more === true`
- Clique no botão: re-chama com `limit=0`

**Tabela de mapeamentos:**
- Coluna "Categoria ML" exibe `ml_category_path (ml_category_id)` ao invés de só o nome

---

## 6. Formato de Dados — Mapeamentos

**Formato atual:**
```json
{"ml_category_id": "MLB420009", "ml_category_name": "Ração", "adsgen_name": "ração aves"}
```

**Formato novo (campo adicionado):**
```json
{"ml_category_id": "MLB420009", "ml_category_name": "Ração", "ml_category_path": "Pet Shop > Aves e Acessórios > Ração", "adsgen_name": "ração aves"}
```

---

## 7. Auto-Migração de Dados Antigos

**Quando:** ao abrir seção de integrações ML

**Lógica:**
1. Frontend verifica se algum mapeamento tem `ml_category_path` ausente/vazio
2. Se sim: `POST /api/ml/categories/migrate-paths` com lista de `ml_category_id`
3. Backend: para cada ID, busca `path_from_root` via `/categories/{id}` (público)
4. Monta path string: `" > ".join([p["name"] for p in path_from_root])`
5. Atualiza mapeamentos no DB
6. Retorna mapeamentos atualizados
7. Frontend recarrega tabela

---

## 8. Endpoints

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/api/ml/categories/tree/status` | Status do cache (`ready`, `loading`, `unavailable`) |
| `GET` | `/api/ml/categories/tree/search?q=...&limit=20` | Busca fuzzy local |
| `POST` | `/api/ml/categories/migrate-paths` | Migra mapeamentos antigos (adiciona path) |

---

## Arquivos Modificados/Criados

| Arquivo | Alteração |
|---------|-----------|
| `mercadolivre_category_tree.py` (novo) | Módulo: carregamento BFS, cache memória, busca fuzzy |
| `app.py` | Model `MercadoLivreCategoryTreeCache`, startup task, 3 endpoints |
| `static/main.html` | Modal com Enter, path completo, "mostrar todos", migração |
| `requirements.txt` | + `rapidfuzz` |
| `alembic/versions/` | Migration para tabela `mercadolivre_category_tree_cache` |

---

## Trade-offs

| Prós | Contras |
|------|---------|
| Busca instantânea e fuzzy após carga | ~60s na primeira carga (sem cache DB) |
| Path completo sempre visível | ~2500 chamadas API na primeira carga |
| Deploy sem re-carga (cache DB 60 dias) | Cache ocupa ~2-5MB em memória |
| Funciona sem API ML após cache | Necessita ao menos 1 conta ML para carga inicial |
| Invalidação manual via DB simples | |
