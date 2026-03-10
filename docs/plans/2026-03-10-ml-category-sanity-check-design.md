# Design: Sanity Check de Atributos Obrigatórios por Categoria ML

**Data:** 2026-03-10
**Contexto:** O ML pode alterar os atributos obrigatórios de uma categoria a qualquer momento. Isso causou o erro `seller_package_dimensions missing` em MLB178930 sem nenhuma mudança no código.

## Problema

1. `SELLER_PACKAGE_*` são `hidden` na API de atributos → filtrados da UI → nunca enviados em `ml_attributes`
2. Para categorias `catalog_required`, o payload não inclui `shipping.dimensions`
3. Sem nenhum caminho para dimensões de pacote chegarem ao ML para catalog/catalog_required
4. Atributos obrigatórios podem mudar sem aviso → sistema não detecta → publicação falha

## Solução

### Tabela `ml_category_baseline`

```
ml_category_baseline
├── id (PK, serial)
├── user_id (UUID, FK → users)
├── category_id (VARCHAR, ex: "MLB178930")
├── required_attr_ids (JSONB) — ["BRAND", "MODEL", ...]
├── conditional_attr_ids (JSONB) — ["UNITS_PER_PACK", "GTIN", ...]
├── hidden_writable_attr_ids (JSONB) — ["SELLER_PACKAGE_HEIGHT", ...] (hidden=true, read_only=false)
├── full_snapshot (JSONB) — snapshot completo para diff detalhado
├── created_at (TIMESTAMP)
├── updated_at (TIMESTAMP)
└── UNIQUE(user_id, category_id)
```

### Novo Step 2: validate_category

Após validar credenciais ML (step 1), o novo step executa:

1. `GET /categories/{category_id}/attributes` (API ML)
2. Extrair: `required_ids`, `conditional_ids`, `hidden_writable_ids`
3. Consultar `ml_category_baseline` no DB

**Se baseline NÃO existe** (primeira publicação na categoria):
- Verificar se todos os required têm valor em `ml_attributes` ou podem ser auto-preenchidos
- Se SIM: salvar baseline, prosseguir
- Se NÃO: emitir SSE `category_validation_failed`, botão "Notificar Devs" → ABORTAR

**Se baseline EXISTE**:
- Comparar required_ids atuais vs salvos
- Se IGUAIS: prosseguir
- Se DIFERENTES: calcular diff (added/removed)
  - Se added podem ser preenchidos: atualizar baseline, prosseguir com aviso
  - Se NÃO: emitir SSE `category_structure_changed` com diff → ABORTAR

**Auto-injeção de hidden_writable**:
- `SELLER_PACKAGE_HEIGHT` ← `height_cm` da UI
- `SELLER_PACKAGE_WIDTH` ← `width_cm` da UI
- `SELLER_PACKAGE_LENGTH` ← `length_cm` da UI
- `SELLER_PACKAGE_WEIGHT` ← `weight_kg × 1000` da UI

### UI no Painel SSE

| Cenário | Ícone | Mensagem | Ação |
|---|---|---|---|
| Validação ok | Check verde | "Categoria validada — N atributos obrigatórios verificados" | Continua |
| Primeira publicação, ok | Info azul | "Primeira publicação em {nome} — estrutura salva como referência" | Continua |
| Mudança auto-resolvível | Warning amarelo | "Estrutura mudou! +N atributos — preenchidos automaticamente" | Continua |
| Mudança não resolvível | Erro vermelho | Diff detalhado + [Notificar Time de Dev] + [Fechar] | ABORTA |

### Notificação WhatsApp

```
⚠️ [Ads Gen] Mudança de categoria ML detectada
Categoria: {category_id} ({category_name})
SKU: {sku}
Novos obrigatórios: {added_list}
Removidos: {removed_list}
Data: {timestamp}
```

## Testes

1. `test_category_baseline_first_publish` — Baseline criado na primeira publicação
2. `test_category_baseline_no_change` — Baseline existe, sem mudanças → continua
3. `test_category_baseline_change_detected` — Diff calculado corretamente
4. `test_category_baseline_auto_inject_seller_package` — SELLER_PACKAGE_* injetados de dimensões da UI
5. `test_category_baseline_abort_missing_fields` — Aborta quando required faltante sem auto-preenchimento
6. `test_payload_snapshot_regression` — Snapshot do payload exato enviado ao create_listing
