# Prompt: Implementar lógica de seleção de imagens para anúncios no Mercado Livre

## Contexto

O sistema "ads gen" publica anúncios no Mercado Livre. As imagens dos produtos ficam em uma pasta no Google Drive, organizadas por SKU. O nome dos arquivos segue convenções que indicam se a imagem é para anúncio simples ou para kits (combos). Preciso implementar a função que, dado um SKU, o tipo de anúncio (simples ou kit com N unidades) e a lista de arquivos disponíveis na pasta, retorne a lista ordenada de imagens a serem usadas no anúncio.

## Convenção de nomenclatura dos arquivos

### Imagens de anúncio simples
```
{SKU}-{SEQ}.{ext}
```
- `{SKU}` é o código do produto, fornecido como input (pode conter letras, números e hifens, ex: `ABC-123`). Não é necessário inferir ou extrair o SKU do nome do arquivo — ele vem pronto no input.
- `{SEQ}` é o número sequencial com zero-padding variável (`1`, `01`, `001` representam a mesma posição: 1)
- `{ext}` é qualquer extensão de arquivo (sempre presente) — não filtrar por extensão específica; aceitar qualquer extensão
- Exemplos: `ABC-123-01.jpg`, `ABC-123-02.png`, `XYZ-03.webp`, `FOTO-1.bmp`

### Imagens de kit/combo
```
{SKU}[-]?CB{X}-{SEQ}.{ext}   (o hífen antes de CB é opcional)
```
- `{X}` é a quantidade de itens do kit (ex: `2`, `3`, `4`)
- `{SEQ}` é o número sequencial com zero-padding variável
- Exemplos válidos (todos aceitos pelo parser):
  - `ABC-123CB2-01.jpg`  → kit de 2, posição 1
  - `ABC-123-CB3-01.png` → kit de 3, posição 1
  - `XYZCB4-001.webp`    → kit de 4, posição 1

## Regras de seleção de imagens

### Passo 1 — Classificar todos os arquivos da pasta

Dado o SKU (input), classificar cada arquivo em:
- **Imagem simples**: casa com o padrão `{SKU}-{SEQ}.{ext}` e NÃO contém `CB` entre o SKU e a sequência
- **Imagem de kit X**: casa com o padrão `{SKU}[-]?CB{X}-{SEQ}.{ext}`
- **Ignorado**: qualquer arquivo que não case com nenhum padrão (inclui outros SKUs, PDFs, etc.)

A comparação de todo o padrão (incluindo SKU e CB) deve ser **case-insensitive**.

### Passo 2 — Montar o array de imagens conforme o tipo de anúncio

#### Anúncio simples
1. Pegar todas as imagens simples.
2. Ordenar por sequência **numérica** ascendente (ATENÇÃO: comparar como número inteiro, não como string. `"2"` vem antes de `"10"`, não depois).
3. Retornar essa lista.
4. Imagens de kit são **totalmente ignoradas**, mesmo que não haja nenhuma imagem simples (nesse caso retorna array vazio).

#### Anúncio kit com X unidades
1. Pegar todas as imagens simples → ordenar por sequência numérica ascendente → este é o **array base**.
2. Pegar todas as imagens de kit com quantidade = X → ordenar por sequência numérica ascendente.
3. **Merge por posição sequencial:**
   - Criar um mapa unificado de `posição numérica → imagem`, onde posição é o valor inteiro da sequência.
   - Primeiro, popular o mapa com todas as imagens simples (chave = sua SEQ como inteiro).
   - Depois, sobrescrever no mapa as posições que possuem imagens de kit X (chave = sua SEQ como inteiro). Imagem de kit **sempre vence** sobre imagem simples na mesma posição.
   - Se uma imagem de kit tem posição que **NÃO existe** no array base de imagens simples → ela é **adicionada** ao mapa nessa posição (NÃO é ignorada).
4. Extrair os valores do mapa, ordenar por posição numérica ascendente.
5. Retornar essa lista final. A posição no anúncio segue a ordem resultante (1ª imagem da lista = 1ª do anúncio, 2ª = 2ª, etc.), independente dos números originais de sequência.
6. Se **nenhuma** imagem de kit X existir → usar o array base completo (apenas imagens simples).
7. Imagens de outros kits (CB com quantidade ≠ X) são **totalmente ignoradas**.
8. Se não houver imagens simples NEM imagens do kit X → retornar array vazio.

### Passo 3 — NÃO truncar
Não impor limite máximo de imagens. A API do Mercado Livre ignora o excesso automaticamente. Enviar todas as imagens selecionadas.

## Assinatura da função

```typescript
type AdType = 'simple' | 'kit';

interface ImageSelectionInput {
  sku: string;               // ex: "ABC-123" — fornecido pronto, usar como-está
  adType: AdType;
  kitSize?: number;          // obrigatório se adType === 'kit'. Ex: 2, 3, 4...
  availableFiles: string[];  // nomes dos arquivos na pasta do Drive (com extensão)
}

interface SelectedImage {
  fileName: string;          // nome original do arquivo
  position: number;          // posição final no anúncio (1-based, sequencial da ordem da lista)
  source: 'simple' | 'kit';  // indica se veio do padrão simples ou kit
}

function selectAdImages(input: ImageSelectionInput): SelectedImage[];
```

## Casos de teste obrigatórios

Implemente testes unitários cobrindo **todos** os cenários abaixo.

### Setup: arquivos disponíveis para SKU = "XPTO"
```
XPTO-01.jpg
XPTO-02.jpg
XPTO-03.png
XPTO-04.webp
XPTOCB2-01.jpg
XPTOCB2-02.png
XPTO-CB3-01.jpg
XPTOCB4-001.jpg
relatorio.pdf        ← ignorado (não casa com padrão de imagem do SKU)
OUTRO-SKU-01.jpg     ← ignorado (SKU diferente)
```

### Teste 1 — Anúncio simples
- Input: `{ sku: "XPTO", adType: "simple", availableFiles: [...] }`
- Expected: `[XPTO-01.jpg, XPTO-02.jpg, XPTO-03.png, XPTO-04.webp]` (nessa ordem)
- Nenhuma imagem CB deve aparecer.

### Teste 2 — Kit com 2
- Input: `{ sku: "XPTO", adType: "kit", kitSize: 2, availableFiles: [...] }`
- Expected: `[XPTOCB2-01.jpg, XPTOCB2-02.png, XPTO-03.png, XPTO-04.webp]`
- Posições 1 e 2 substituídas pelas imagens do kit. Posições 3 e 4 mantidas do simples.

### Teste 3 — Kit com 3
- Input: `{ sku: "XPTO", adType: "kit", kitSize: 3, availableFiles: [...] }`
- Expected: `[XPTO-CB3-01.jpg, XPTO-02.jpg, XPTO-03.png, XPTO-04.webp]`
- Apenas posição 1 substituída.

### Teste 4 — Kit com 4
- Input: `{ sku: "XPTO", adType: "kit", kitSize: 4, availableFiles: [...] }`
- Expected: `[XPTOCB4-001.jpg, XPTO-02.jpg, XPTO-03.png, XPTO-04.webp]`
- Apenas posição 1 substituída.

### Teste 5 — Kit com 5 (sem imagens específicas)
- Input: `{ sku: "XPTO", adType: "kit", kitSize: 5, availableFiles: [...] }`
- Expected: `[XPTO-01.jpg, XPTO-02.jpg, XPTO-03.png, XPTO-04.webp]`
- Fallback completo para imagens simples.

### Teste 6 — Kit com imagem em posição inexistente no simples (APPEND)
- Setup: adicionar `XPTOCB2-09.jpg` aos arquivos do setup base
- Input: `{ sku: "XPTO", adType: "kit", kitSize: 2, availableFiles: [...] }`
- Expected: `[XPTOCB2-01.jpg, XPTOCB2-02.png, XPTO-03.png, XPTO-04.webp, XPTOCB2-09.jpg]`
- CB2-09 é ADICIONADA ao mapa na posição 9. Após ordenar numericamente: posições 1, 2, 3, 4, 9 → resultado final tem 5 imagens.

### Teste 7 — Sequência com furos (ordenação numérica, não alfabética)
- Setup: arquivos `XPTO-01.jpg, XPTO-02.jpg, XPTO-05.jpg, XPTO-09.jpg, XPTO-10.jpg, XPTO-100.jpg`
- Input: `{ sku: "XPTO", adType: "simple", availableFiles: [...] }`
- Expected: `[XPTO-01.jpg, XPTO-02.jpg, XPTO-05.jpg, XPTO-09.jpg, XPTO-10.jpg, XPTO-100.jpg]`
- CRÍTICO: a ordem DEVE ser 1, 2, 5, 9, 10, 100 — e NÃO 1, 10, 100, 2, 5, 9 (ordenação string).

### Teste 8 — SKU com hifens (ex: "AB-CD-123")
- Setup: arquivos `AB-CD-123-01.jpg`, `AB-CD-123-02.jpg`, `AB-CD-123CB2-01.jpg`
- Input: `{ sku: "AB-CD-123", adType: "kit", kitSize: 2, availableFiles: [...] }`
- Expected: `[AB-CD-123CB2-01.jpg, AB-CD-123-02.jpg]`

### Teste 9 — Case insensitive para CB e SKU
- Setup: `xptocb2-01.jpg` e `XPTOCb2-02.jpg` (SKU input = "XPTO")
- Input: `{ sku: "XPTO", adType: "kit", kitSize: 2, availableFiles: [...] }`
- Expected: ambas reconhecidas como imagens de kit 2 e usadas.

### Teste 10 — Sem nenhuma imagem relevante
- Setup: `relatorio.pdf, OUTRO-01.jpg`
- Input: `{ sku: "XPTO", adType: "simple", availableFiles: [...] }`
- Expected: array vazio `[]`

### Teste 11 — Sem imagens simples, com imagens de kit → anúncio SIMPLES
- Setup: apenas `XPTOCB2-01.jpg` e `XPTOCB2-02.jpg`
- Input: `{ sku: "XPTO", adType: "simple", availableFiles: [...] }`
- Expected: array vazio `[]`
- Anúncio simples NUNCA usa imagens de kit.

### Teste 12 — Sem imagens simples, com imagens de kit → anúncio KIT
- Setup: apenas `XPTOCB2-01.jpg` e `XPTOCB2-02.jpg`
- Input: `{ sku: "XPTO", adType: "kit", kitSize: 2, availableFiles: [...] }`
- Expected: `[XPTOCB2-01.jpg, XPTOCB2-02.jpg]`
- As imagens de kit formam o array sozinhas quando não há imagens simples.

### Teste 13 — Qualquer extensão aceita
- Setup: `XPTO-01.tiff`, `XPTO-02.bmp`, `XPTO-03.gif`, `XPTO-04.svg`
- Input: `{ sku: "XPTO", adType: "simple", availableFiles: [...] }`
- Expected: todos os 4 arquivos aceitos e retornados em ordem. Arquivos sempre têm extensão — o parser pode assumir isso.

### Teste 14 — Zero-padding normalizado (colisão de posição)
- Setup: `XPTO-1.jpg`, `XPTO-01.png`, `XPTO-001.webp` ← todos representam posição 1
- Input: `{ sku: "XPTO", adType: "simple", availableFiles: [...] }`
- Expected: CONFLITO — 3 arquivos na mesma posição numérica. Usar o **primeiro encontrado na lista de input** (ordem estável) e ignorar os duplicados. Retornar apenas 1 imagem na posição 1.

## Restrições de implementação

1. A função deve ser **pura** (sem side-effects, sem I/O). A obtenção da lista de arquivos do Drive é responsabilidade do caller.
2. O regex de parsing deve ser construído **dinamicamente** com base no SKU fornecido, escapando caracteres especiais do SKU para uso em regex.
3. Manter a lógica de parsing e a lógica de seleção em funções separadas para testabilidade.
4. Toda a implementação deve ter **100% de cobertura** nos testes listados acima.
5. Integrar a função no ponto do código onde hoje as imagens são carregadas para o anúncio no Meli — localizar esse trecho no codebase e substituir/adaptar.

## Notas de revisão

⚠️ **ANTES de implementar**, verifique no codebase:
- Qual linguagem/framework é usado (TypeScript? Python?). Adapte a assinatura e testes conforme.
- Onde no fluxo de publicação as imagens são selecionadas/carregadas hoje.
- Se já existe alguma lógica de nomeação de imagens que precisa ser substituída.
