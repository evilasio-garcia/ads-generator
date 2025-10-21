# Guia de Teste - Sistema de Upload de Arquivos

## Cenários de Teste

### Cenário 1: Upload de 15 arquivos válidos
**Ação:** Fazer upload de 15 imagens válidas (PNG/JPG)

**Resultado Esperado:**
- Arquivos 1-10: Badge verde "✓ Será usado (1/10)" até "(10/10)"
- Primeira coluna verde (ícone de arrastar)
- Checkbox marcado e habilitado

- Arquivos 11-15: Badge cinza "Não será usado"
- Primeira coluna cinza
- Checkbox desmarcado e DESABILITADO
- Mensagem: "Limite de 10 arquivos atingido"

### Cenário 2: Desmarcar arquivo #3
**Ação:** Desmarcar o checkbox do arquivo na posição 3

**Resultado Esperado:**
- Arquivo #3: Badge azul "○ Aguardando seleção"
- Primeira coluna azul
- Checkbox desmarcado mas HABILITADO
- Mensagem: "Marque o checkbox para usar"

- **TODOS** os arquivos 11-15: Badge azul "○ Aguardando seleção"
- Primeira coluna azul
- Checkbox desmarcado mas HABILITADO
- Mensagem: "Marque o checkbox para usar"

- Arquivos 1-2, 4-10: Continuam verdes "✓ Será usado (X/10)"
- Numeração atualiza: agora mostra (1/10), (2/10), (3/10)... (9/10) pulando o #3

### Cenário 3: Marcar arquivo #11
**Ação:** Marcar o checkbox do arquivo #11 que está azul

**Resultado Esperado:**
- Arquivo #11: Badge verde "✓ Será usado (10/10)"
- Primeira coluna verde
- Checkbox marcado

- **TODOS** os arquivos 12-15: Perdem slot automaticamente
- Badge cinza "Não será usado"
- Primeira coluna cinza
- Checkbox desmarcado e DESABILITADO

- Arquivo #3: Perde slot também (agora 10 arquivos marcados)
- Badge cinza "Não será usado"
- Primeira coluna cinza
- Checkbox desmarcado e DESABILITADO

### Cenário 4: Desmarcar 3 arquivos do top 10
**Ação:** Desmarcar arquivos #2, #5, e #8

**Resultado Esperado:**
- Arquivos #2, #5, #8: Azul "○ Aguardando seleção"
- **TODOS** arquivos 11-15: Ganham slots automaticamente (azul, aguardando)
- Total processado: 7 arquivos (os 7 que ficaram marcados)
- Numeração: (1/10), (2/10)... (7/10) apenas nos marcados
- Agora há 8 arquivos com slot disponível esperando seleção (#2, #5, #8, #11-15)

### Cenário 5: Marcar arquivos #11, #12, e #13
**Ação:** Marcar 3 dos arquivos que estão aguardando seleção

**Resultado Esperado:**
- Arquivos #11, #12, #13: Verde "✓ Será usado"
- Total processado: 10 arquivos
- **TODOS** arquivos não-marcados: PERDEM slots automaticamente
  - Arquivos #2, #5, #8, #14, #15: Cinza "Não será usado"
  - Checkboxes desabilitados

### Cenário 6: Arrastar arquivo #15 para posição #1
**Ação:** Arrastar o último arquivo para o topo

**Resultado Esperado:**
- Arquivo arrastado assume posição #1
- Se está marcado: Mantém verde "✓ Será usado (1/10)"
- Se não está marcado mas há slots: Ganha slot azul "○ Aguardando seleção"
- Sistema recalcula TODA a lista após o drag
- Arquivo que estava na posição #10 pode perder slot se não estiver marcado

### Cenário 7: Upload de arquivo inválido
**Ação:** Fazer upload de arquivo .pdf ou arquivo > 5MB

**Resultado Esperado:**
- Badge vermelho "❌ Inválido"
- Primeira coluna vermelha
- Mensagem de erro (ex: "Tipo não suportado" ou "Arquivo muito grande")
- Checkbox desmarcado e DESABILITADO
- Apenas botão "Remover" disponível

## Validações Importantes

### ✅ Limite de 10 arquivos processados
- NUNCA deve mostrar mais de 10 badges verdes
- NUNCA deve mostrar números acima de (10/10)
- Números devem ser sequenciais: (1/10), (2/10), ..., (10/10)

### ✅ Gerenciamento de slots
- Total de slots = 10
- Slots ocupados = arquivos marcados (verdes)
- Slots disponíveis = 10 - slots ocupados
- Arquivos "aguardando" (azuis) = slots disponíveis preenchidos em ordem

### ✅ Cores corretas
- 🟢 Verde: Será processado (marcado, dentro do limite de 10)
- 🔵 Azul: Slot disponível, aguardando seleção (checkbox habilitado, não marcado)
- ⚪ Cinza: Sem slot, não será usado (checkbox desabilitado)
- 🔴 Vermelho: Arquivo inválido (checkbox desabilitado)

### ✅ Estados de checkbox
- Habilitado + Marcado = Verde (será processado)
- Habilitado + Desmarcado = Azul (aguardando)
- Desabilitado + Desmarcado = Cinza (sem slot) ou Vermelho (inválido)

## Bugs a Evitar

❌ Mostrar 15 arquivos como "Será usado (4/10)" até "(18/10)" - ERRADO!
✅ Mostrar apenas 10 arquivos como "Será usado (1/10)" até "(10/10)" - CORRETO!

❌ Permitir marcar mais de 10 arquivos
✅ Desabilitar checkboxes quando 10 slots estão ocupados

❌ Não atualizar slots ao desmarcar arquivo
✅ Automaticamente promover próximo arquivo válido para slot disponível
