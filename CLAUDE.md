# Regras do Projeto — adsGenerator

## Testes
- Antes de considerar qualquer tarefa concluída, rodar todos os testes automatizados com:
  ```
  python -m pytest tests/
  ```
  **Importante:** usar `python -m pytest` (não `pytest` diretamente) para garantir o uso do `.venv` do projeto. O `pytest` do PATH do sistema não carrega o `pytest-asyncio` corretamente.
- Só entregar o resultado como pronto se todos os testes passarem.
- Se algum teste falhar, investigar e corrigir antes de finalizar.
