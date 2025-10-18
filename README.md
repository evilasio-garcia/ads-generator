# Ads Generator — Gerador de Anúncios Multi‑Marketplace

MVP auto-contido em **Python + FastAPI** para gerar **título, descrição, FAQ (10)** e **11 cards** otimizados por marketplace (Amazon, Mercado Livre, Shopee, Magalu, Shein).

- UI: `index.html` (Tailwind, modal de configurações salvas em `localStorage`).
- API: `app.py` (`/api/generate`, validação por regra de marketplace, modo MOCK quando não há chaves LLM).
- Deploy recomendado: **Render** (free/low cost).
- Futuro: persistir configs/histórico no **Supabase** (schema `adgen`).

## Rodando localmente

```bash
python -m venv .venv
./.venv/Scripts/activate   # Windows
# source .venv/bin/activate # macOS/Linux

pip install -r requirements.txt
uvicorn app:app --reload --port 8000
# Abra http://localhost:8000
```

### Chaves (opcional)

```bash
# OpenAI
setx OPENAI_API_KEY "sk-..."
setx OPENAI_BASE_URL "https://api.openai.com/v1"
setx MODEL_OPENAI "gpt-4.1-mini"

# Gemini
setx GEMINI_API_KEY "AIza..."
setx GEMINI_BASE_URL "https://generativelanguage.googleapis.com"
setx MODEL_GEMINI "gemini-1.5-pro"
```

Sem chaves → **modo MOCK** com conteúdo de exemplo.

## Estrutura

```
.
├── app.py
├── requirements.txt
├── .gitignore
├── README.md
└── static/
    └── index.html
```

## Git (inicial)

```bash
git init
git add .
git commit -m "feat: initial commit — Ads Generator MVP (FastAPI + Tailwind UI)"
git branch -M main
git remote add origin https://github.com/SEU-USUARIO/ads-generator.git
git push -u origin main
```
