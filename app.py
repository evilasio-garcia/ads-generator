# -*- coding: utf-8 -*-
import base64
import asyncio
import hashlib
import json
import logging
import os
import random
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx
import requests
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, create_engine, inspect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

# Importar serviço Tiny
import tiny_service
import canva_service
import mercadolivre_service
from auth_helpers import gateway_login_helper, CurrentUser, get_current_user_master
from config import settings
# Importar pricing module
from pricing import PriceCalculatorFactory
from pricing import ml_shipping

app = FastAPI(title="Ads Generator API", version="2.2.0")
logger = logging.getLogger("ads_generator.workspace")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    ProxyHeadersMiddleware,
    trusted_hosts="*",
)

# Static
if not os.path.isdir("static"):
    os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static", html=True), name="static")
# -----------------------------------------------------------------------------
# Database configuration for persistent user settings
# -----------------------------------------------------------------------------

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://app_ads_generator_usr:app_ads_generator_psw@localhost:5432/app_ads_generator_db",
)

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class UserConfig(Base):
    __tablename__ = "user_config"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, index=True, nullable=False)
    data = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class SkuWorkspace(Base):
    __tablename__ = "sku_workspace"
    __table_args__ = (
        UniqueConstraint("sku_normalized", "marketplace_normalized", name="ux_sku_workspace_sku_marketplace"),
    )

    id = Column(String, primary_key=True, default=lambda: uuid4().hex)
    sku_normalized = Column(String, index=True, nullable=False)
    marketplace_normalized = Column(String, index=True, nullable=False)
    sku_display = Column(String, nullable=False)
    base_state = Column(JSONB, nullable=False, default=dict)
    versioned_state_current = Column(JSONB, nullable=False, default=dict)
    state_seq = Column(Integer, nullable=False, default=0)
    created_by_user_id = Column(String, nullable=False)
    updated_by_user_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    last_accessed_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SkuWorkspaceHistory(Base):
    __tablename__ = "sku_workspace_history"

    id = Column(String, primary_key=True, default=lambda: uuid4().hex)
    workspace_id = Column(String, ForeignKey("sku_workspace.id"), index=True, nullable=False)
    seq = Column(Integer, nullable=False)
    action = Column(String, nullable=False)
    created_by_user_id = Column(String, nullable=False)
    versioned_state_snapshot = Column(JSONB, nullable=False, default=dict)
    snapshot_hash = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class TinyKitResolution(Base):
    __tablename__ = "tiny_kit_resolution"
    __table_args__ = (
        UniqueConstraint("sku_root_normalized", "kit_quantity", name="ux_tiny_kit_resolution_sku_qty"),
    )

    id = Column(String, primary_key=True, default=lambda: uuid4().hex)
    sku_root_normalized = Column(String, index=True, nullable=False)
    kit_quantity = Column(Integer, nullable=False)
    resolved_sku = Column(String, nullable=False)
    validation_source = Column(String, nullable=False, default="pattern_skucb")
    unit_plural_override = Column(String, nullable=True)
    tiny_product_id = Column(String, nullable=True)
    validation_snapshot = Column(JSONB, nullable=False, default=dict)
    validated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_checked_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_schema_ready() -> None:
    inspector = inspect(engine)
    required = {"alembic_version", "user_config", "sku_workspace", "sku_workspace_history", "tiny_kit_resolution"}
    existing = set(inspector.get_table_names())
    missing = sorted(required - existing)
    if missing:
        raise RuntimeError(
            "Schema desatualizado. Tabelas ausentes: "
            + ", ".join(missing)
            + ". Execute: alembic upgrade head"
        )
    sku_workspace_columns = {col["name"] for col in inspector.get_columns("sku_workspace")}
    if "marketplace_normalized" not in sku_workspace_columns:
        raise RuntimeError(
            "Schema desatualizado. Coluna ausente: sku_workspace.marketplace_normalized. "
            "Execute: alembic upgrade head"
        )


@app.on_event("startup")
def _startup_schema_guard() -> None:
    _ensure_schema_ready()


@app.get("/", include_in_schema=False)
async def root_index(current_user: CurrentUser = Depends(get_current_user_master)):
    # Serve the SPA from /static/main.html
    index_path = os.path.join("static", "main.html")
    return FileResponse(index_path)


class Options(BaseModel):
    llm: str = Field("openai", description="openai | gemini")
    openai_api_key: str = ""
    openai_base_url: str = ""
    gemini_api_key: str = ""
    gemini_base_url: str = ""
    rules: Dict[str, Any] = Field(default_factory=dict)
    prompt_template: Optional[str] = None
    tiny_product_data: Optional[Dict[str, Any]] = None
    variation_context: Optional[Dict[str, Any]] = None


class GenerateIn(BaseModel):
    product_name: str
    marketplace: str
    options: Options


class RegenIn(BaseModel):
    product_name: str
    marketplace: str
    field: str  # title | description | faq_item | card
    index: Optional[int] = None
    prompt: str = ""
    context: Dict[str, Any] = Field(default_factory=dict)
    options: Options


DEFAULT_PROMPT_TEMPLATE = (
    'Produto: "{product}"\n'
    "Marketplace: {marketplace}\n"
    "Especificações agregadas (parciais e possivelmente ruidosas): {specs}\n\n"
    "Tarefas:\n"
    "1) TÍTULO (apenas texto, 1 linha) — incluir marca, atributo-chave e variante quando relevante.\n"
    "2) DESCRIÇÃO (sem emojis; clara, escaneável; 3-6 bullets iniciais + 3-5 parágrafos).\n"
    "3) FAQ (10 pares Q->A) – foque objeções reais, uso, compatibilidades, garantia, manutenção, devolução.\n"
    '4) CARDS (11 itens) – para imagens 1200x1200: cada item = { "title": "...", "text": "..." } curto e direto.\n\n'
    "Restrições:\n"
    '- Sem "frete grátis", "brinde", "promoção" ou equivalentes.\n'
    "- Não use emojis. Escreva em português do Brasil.\n"
    "- Adapte o tom ao marketplace especificado.\n"
    "- Responda em JSON com as chaves: title, description, faq (array de objetos {q,a}), cards (array de objetos {title,text}).\n"
)


class ConfigPayload(BaseModel):
    openai: str = ""
    openai_base: str = ""
    gemini: str = ""
    gemini_base: str = ""
    rules: Dict[str, Any] = Field(default_factory=dict)
    prompt_template: Optional[str] = None
    tiny_tokens: List[Dict[str, Any]] = Field(default_factory=list)
    pricing_config: List[Dict[str, Any]] = Field(default_factory=list)
    image_search: Dict[str, Any] = Field(default_factory=dict)
    google_drive: Dict[str, Any] = Field(default_factory=dict)
    canva: Dict[str, Any] = Field(default_factory=dict)
    general: Dict[str, Any] = Field(default_factory=dict)


def _default_config_payload() -> Dict[str, Any]:
    return {
        "openai": "",
        "openai_base": "",
        "gemini": "",
        "gemini_base": "",
        "rules": {},
        "prompt_template": DEFAULT_PROMPT_TEMPLATE,
        "tiny_tokens": [],
        "pricing_config": [],
        "image_search": {"api_key": "", "cx": ""},
        "google_drive": {"folder_id": "", "credentials_json": "", "auth_type": "service_account"},
        "canva": {"client_id": "", "client_secret": ""},
        "general": {
            "kit_name_replacements": [
                {"from": " C/ ", "to": " COM "},
                {"from": " S/ ", "to": " SEM "},
                {"from": " PCT ", "to": " PACOTE "},
                {"from": " CX ", "to": " CAIXA "},
                {"from": " UNID ", "to": " UNIDADE "},
            ]
        },
    }


def _extract_kit_name_replacements_from_config(
    raw_config: Optional[Dict[str, Any]],
) -> List[Dict[str, str]]:
    default_items = (
        _default_config_payload()
        .get("general", {})
        .get("kit_name_replacements", [])
    )
    cfg = raw_config if isinstance(raw_config, dict) else {}
    general = cfg.get("general") if isinstance(cfg.get("general"), dict) else {}
    raw_items = general.get("kit_name_replacements")

    normalized: List[Dict[str, str]] = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            from_text = str(item.get("from") or "")
            to_text = str(item.get("to") or "")
            if not from_text.strip():
                continue
            normalized.append({"from": from_text, "to": to_text})

    if normalized:
        return normalized

    fallback: List[Dict[str, str]] = []
    for item in default_items:
        if not isinstance(item, dict):
            continue
        from_text = str(item.get("from") or "")
        to_text = str(item.get("to") or "")
        if not from_text.strip():
            continue
        fallback.append({"from": from_text, "to": to_text})
    return fallback


def render_prompt_template(tpl: str, product: str, marketplace: str, specs: str) -> str:
    """
    Protege chaves literais do template e expande apenas {product}, {marketplace}, {specs}.
    Não exige que você duplique chaves em exemplos de JSON.
    """
    tok_product = "%%__PRODUCT__%%"
    tok_marketplace = "%%__MARKETPLACE__%%"
    tok_specs = "%%__SPECS__%%"

    # 1) Marcar placeholders reais
    tmp = (tpl
           .replace("{product}", tok_product)
           .replace("{marketplace}", tok_marketplace)
           .replace("{specs}", tok_specs))

    # 2) Escapar TODAS as demais chaves literais do template
    tmp = tmp.replace("{", "{{").replace("}", "}}")

    # 3) Restaurar os placeholders com os valores
    tmp = (tmp
           .replace(tok_product, product)
           .replace(tok_marketplace, marketplace)
           .replace(tok_specs, specs))
    return tmp


def ensure_plain_text_desc(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[*`_]{1,3}", "", text)
    text = re.sub(r"^[ \t]*#{1,6}[ \t]*", "", text, flags=re.MULTILINE)
    lines = []
    for line in text.splitlines():
        l = line.rstrip()
        if re.match(r"^[ \t]*[-*•][ \t]+", l):
            l = re.sub(r"^[ \t]*[-*•][ \t]+", "• ", l)
        lines.append(l)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def mock_cards(term: str):
    base = [
        ("Material durável", "Feito em PP/PVC resistente e fácil de limpar."),
        ("Medida ideal", "80×60 cm: compatível com diferentes ambientes."),
        ("Com peneira", "Facilita a remoção dos resíduos no dia a dia."),
        ("Leve e prático", "Transporte e movimentação sem esforço."),
        ("Design moderno", "Combina com a decoração da sua casa."),
        ("Higiênico", "Use água e sabão neutro na limpeza."),
        ("Versátil", "Compatível com padrões de uso de diversos pets."),
        ("Conforto", "Acabamento liso e agradável ao toque."),
        ("Garantia", "90 dias contra defeitos de fabricação."),
        ("Suporte", "Dúvidas? Atendimento rápido pós‑compra."),
        ("Compra segura", "Devolução conforme política do marketplace."),
    ]
    random.shuffle(base)
    return [{"title": t, "text": x} for t, x in base]


def mock_faq():
    base = [
        ("Serve para todos os gatos?", "Compatível com a maioria dos portes; verifique as medidas."),
        ("Como faço a limpeza?", "Use água e sabão neutro. Evite abrasivos."),
        ("Possui garantia?", "Sim, 90 dias contra defeitos de fabricação."),
        ("O material é resistente?", "PP/PVC leve, resistente e fácil de limpar."),
        ("Acompanha peneira?", "Sim, inclui bandeja com peneira."),
        ("Qual o tamanho?", "Aproximadamente 80×60 cm."),
        ("É escorregadio?", "Base com boa estabilidade em superfícies planas."),
        ("Aceita devolução?", "Sim, conforme política do marketplace."),
        ("Pode ficar ao ar livre?", "Prefira uso em ambiente interno coberto."),
        ("Como é a montagem?", "Pronto para uso, com instruções simples."),
    ]
    return [{"q": q, "a": a} for q, a in base]


def mock_generate(term: str, marketplace: str):
    title = f"{term} — Design prático, material resistente"
    bullets = [
        "• Material PP/PVC resistente e fácil de limpar",
        "• Medidas 80×60 cm, compatível com diversos ambientes",
        "• Bandeja com peneira que separa resíduos",
        "• Leve e prática para movimentar e higienizar",
        "• Visual moderno que combina com a casa",
    ]
    paragraphs = [
        "A {term} é ideal para garantir conforto e higiene para o seu pet. Com design prático e funcional, facilita a limpeza e a manutenção do ambiente.",
        "O material PP/PVC oferece leveza, resistência e alta durabilidade, mantendo o produto bonito por mais tempo.",
        "A bandeja com peneira contribui para a rotina de cuidados ao permitir a separação dos resíduos de forma rápida.",
        "A limpeza pode ser feita com água e sabão neutro. Para maior conservação, evite produtos abrasivos.",
        "Garantia de 90 dias contra defeitos de fabricação e devolução conforme as políticas do marketplace.",
    ]
    desc = "\n".join(bullets + [""] + [p.replace("{term}", term) for p in paragraphs])
    desc = ensure_plain_text_desc(desc)
    return {
        "title": title,
        "description": desc,
        "faq": mock_faq(),
        "cards": mock_cards(term),
        "sources_used": {"mock": True, "message": "Sem chave de API válida; exibindo conteúdo de exemplo."},
    }


def have_openai(opts: Options) -> bool:
    return bool(opts.openai_api_key.strip())


def have_gemini(opts: Options) -> bool:
    return bool(opts.gemini_api_key.strip())


def call_openai(prompt: str, opts: Options, files_data: Optional[List[Dict[str, Any]]] = None) -> str:
    base = opts.openai_base_url.strip() or "https://api.openai.com/v1"
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {opts.openai_api_key}", "Content-Type": "application/json"}

    # Construir conteúdo com arquivos se houver
    if files_data and len(files_data) > 0:
        # noinspection PyListCreation
        content_parts = []
        # Adicionar prompt de texto
        content_parts.append({"type": "text", "text": prompt})

        # Adicionar imagens (OpenAI suporta imagens via vision)
        for file_info in files_data:
            if file_info['mime_type'].startswith('image/'):
                content_parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{file_info['mime_type']};base64,{file_info['base64_data']}"
                    }
                })
            elif file_info['mime_type'] == 'text/plain':
                # Para arquivos de texto, adicionar ao prompt
                text_content = file_info.get('text_content', '')
                content_parts.append({
                    "type": "text",
                    "text": f"\n\n[Conteúdo do arquivo {file_info['filename']}]:\n{text_content}"
                })

        payload = {"model": "gpt-4o", "messages": [{"role": "user", "content": content_parts}], "temperature": 0.7,
                   "max_tokens": 4096}
    else:
        payload = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "temperature": 0.7}

    last_error = None
    for attempt in range(3):
        if attempt > 0:
            time.sleep(attempt)
        try:
            with requests.Session() as session:
                r = session.post(url, headers=headers, json=payload, timeout=90)
                r.raise_for_status()
                data = r.json()
                return data["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as e:
            last_error = e
    raise HTTPException(status_code=502, detail=f"LLM gateway indisponível após 3 tentativas: {last_error}")


def call_gemini(prompt: str, opts: Options, files_data: Optional[List[Dict[str, Any]]] = None) -> str:
    base = opts.gemini_base_url.strip() or "https://generativelanguage.googleapis.com"
    url = f"{base}/v1/models/gemini-1.5-flash:generateContent?key={opts.gemini_api_key}"

    # Construir partes do conteúdo
    parts = [{"text": prompt}]

    if files_data and len(files_data) > 0:
        for file_info in files_data:
            if file_info['mime_type'].startswith('image/'):
                parts.append({
                    "inlineData": {
                        "mimeType": file_info['mime_type'],
                        "data": file_info['base64_data']
                    }
                })
            elif file_info['mime_type'] == 'text/plain':
                text_content = file_info.get('text_content', '')
                parts.append({
                    "text": f"\n\n[Conteúdo do arquivo {file_info['filename']}]:\n{text_content}"
                })

    payload = {"contents": [{"parts": parts}]}
    last_error = None
    for attempt in range(3):
        if attempt > 0:
            time.sleep(attempt)
        try:
            with requests.Session() as session:
                r = session.post(url, json=payload, timeout=90)
                r.raise_for_status()
                data = r.json()
                try:
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                except Exception:
                    return json.dumps(data)
        except requests.exceptions.RequestException as e:
            last_error = e
    raise HTTPException(status_code=502, detail=f"LLM gateway indisponível após 3 tentativas: {last_error}")


def parse_json_loose(s: str) -> Dict[str, Any]:
    # noinspection RegExpRedundantEscape
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {}


def build_full_prompt(product: str, marketplace: str, opts: Options) -> str:
    tpl = opts.prompt_template or DEFAULT_PROMPT_TEMPLATE
    specs = "{}"
    base_prompt = render_prompt_template(tpl, product, marketplace, specs)

    variation_ctx = _to_safe_dict(opts.variation_context)
    quantity = int(_coerce_float(variation_ctx.get("quantity"), 1.0))
    quantity = max(1, min(quantity, 5))
    variant_key = str(variation_ctx.get("variant_key") or "simple").strip().lower()
    derived_cost = _coerce_float(variation_ctx.get("derived_cost_base"), 0.0)
    derived_width = _coerce_float(variation_ctx.get("derived_width_cm"), 0.0)
    derived_weight = _coerce_float(variation_ctx.get("derived_weight_kg"), 0.0)
    if variation_ctx:
        base_prompt += "\n\n🎯 CONTEXTO DE VARIAÇÃO DO ANÚNCIO:\n"
        base_prompt += f"- Variante ativa: {variant_key}\n"
        base_prompt += f"- Quantidade de itens no anúncio: {quantity}\n"
        if derived_cost > 0:
            base_prompt += f"- Custo base de referência da variante: R$ {derived_cost:.4f}\n"
        if derived_width > 0:
            base_prompt += f"- Largura de referência da variante: {derived_width:.4f} cm\n"
        if derived_weight > 0:
            base_prompt += f"- Peso de referência da variante: {derived_weight:.4f} kg\n"
        if quantity > 1:
            base_prompt += (
                "- Gere conteúdo explícito para KIT/COMBO desta quantidade. "
                "Não reutilize texto do anúncio simples; descreva benefícios e contexto da quantidade.\n"
            )
        else:
            base_prompt += "- Gere conteúdo específico para unidade simples (não kit).\n"

    return base_prompt


def build_field_prompt(base_prompt: str, field: str, previous: Optional[Dict[str, Any]] = None,
                       user_hint: str = "") -> str:
    suffix = "\n\nIMPORTANTE: "
    if field == "title":
        suffix += "Gere APENAS JSON com 'title'. 1 linha, políticas do marketplace, variação diferente do anterior."
    elif field == "description":
        suffix += "Gere APENAS JSON com 'description' mantendo 3-6 bullets (com '• ' no início) + 3-5 parágrafos. Sem emojis/markdown. Preserve quebras de linha."
    elif field == "faq_item":
        suffix += "Gere APENAS JSON com 'faq' contendo 1 objeto {q,a} curto e objetivo."
    elif field == "card":
        suffix += "Gere APENAS JSON com 'cards' contendo 1 objeto {title,text}. Texto curto (<= ~14 palavras)."
    if previous:
        if user_hint:
            # Se há prompt do usuário, deve MELHORAR e COMPLETAR com as novas informações
            suffix += f"\nConteúdo atual a ser melhorado e completado: {json.dumps(previous, ensure_ascii=False)}"
        else:
            # Se não há prompt, gerar variação SIGNIFICATIVAMENTE diferente
            suffix += f"\nVERSÃO ANTERIOR (NÃO repetir): {json.dumps(previous, ensure_ascii=False)}"
            suffix += "\nGere conteúdo OBRIGATORIAMENTE DIFERENTE da versão anterior. Use palavras, estrutura e ângulo completamente novos. NUNCA repita o mesmo texto."
    if user_hint:
        suffix += f"\nInstruções do usuário (use ESTAS informações para melhorar e completar o conteúdo atual): {user_hint}"
    return base_prompt + suffix


def call_model_json(prompt: str, opts: Options, files_data: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    text = ""
    if have_openai(opts):
        text = call_openai(prompt, opts, files_data)
    elif have_gemini(opts):
        text = call_gemini(prompt, opts, files_data)
    else:
        return {}
    return parse_json_loose(text)


async def process_uploaded_files(files: List[UploadFile]) -> tuple[List[Dict[str, Any]], List[str]]:
    """
    Processa arquivos uploaded e retorna lista com dados base64 e informações.
    Retorna (files_data, warnings) onde warnings são mensagens sobre arquivos ignorados.
    """
    files_data = []
    warnings = []

    # Limites de segurança
    # noinspection PyPep8Naming
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB por arquivo
    # noinspection PyPep8Naming
    MAX_FILES = 10  # Máximo de arquivos
    # noinspection PyPep8Naming
    MAX_TOTAL_SIZE = 20 * 1024 * 1024  # 20MB total

    # Tipos de arquivo aceitos
    # noinspection PyPep8Naming
    ALLOWED_TYPES = ['image/png', 'image/jpeg', 'image/jpg', 'image/gif', 'image/webp', 'text/plain']

    # Validar número de arquivos
    if len(files) > MAX_FILES:
        warnings.append(f"❌ Muitos arquivos enviados (máx. {MAX_FILES}). Apenas os primeiros serão processados.")
        files = files[:MAX_FILES]

    total_size = 0
    for file in files:
        content = await file.read()
        mime_type = file.content_type or "application/octet-stream"
        file_size = len(content)

        # Validar tamanho individual
        if file_size > MAX_FILE_SIZE:
            warnings.append(f"❌ {file.filename}: arquivo muito grande (máx. 5MB)")
            continue

        # Validar tamanho total
        if total_size + file_size > MAX_TOTAL_SIZE:
            warnings.append(f"❌ {file.filename}: limite total de tamanho atingido (máx. 20MB total)")
            break

        # Validar tipo
        if mime_type not in ALLOWED_TYPES:
            warnings.append(f"⚠️ {file.filename}: tipo não suportado ({mime_type})")
            continue

        file_info = {
            "filename": file.filename,
            "mime_type": mime_type,
            "base64_data": base64.b64encode(content).decode('utf-8'),
        }

        # Se for texto, decodificar para incluir no prompt
        if mime_type == 'text/plain':
            try:
                file_info["text_content"] = content.decode('utf-8')
                # Limitar texto a 10k caracteres
                if len(file_info["text_content"]) > 10000:
                    file_info["text_content"] = file_info["text_content"][:10000] + "\n[...truncado...]"
            except:
                warnings.append(f"⚠️ {file.filename}: erro ao decodificar texto")
                continue

        files_data.append(file_info)
        total_size += file_size

    return files_data, warnings


def build_full_prompt_with_files(product: str, marketplace: str, opts: Options, has_files: bool = False) -> str:
    """Constrói prompt com instruções específicas sobre uso de arquivos"""
    base_prompt = build_full_prompt(product, marketplace, opts)

    # Injetar dados do Tiny ERP se disponíveis
    if opts.tiny_product_data:
        tiny_data = opts.tiny_product_data
        base_prompt += "\n\n📦 DADOS OFICIAIS DO TINY ERP (USE ESTES DADOS REAIS):\n"

        if tiny_data.get('height_cm') or tiny_data.get('width_cm') or tiny_data.get('length_cm'):
            dims = []
            if tiny_data.get('height_cm'):
                dims.append(f"Altura: {tiny_data['height_cm']} cm")
            if tiny_data.get('width_cm'):
                dims.append(f"Largura: {tiny_data['width_cm']} cm")
            if tiny_data.get('length_cm'):
                dims.append(f"Comprimento: {tiny_data['length_cm']} cm")
            base_prompt += f"- Dimensões: {', '.join(dims)}\n"

        if tiny_data.get('weight_kg'):
            base_prompt += f"- Peso: {tiny_data['weight_kg']} kg\n"

        if tiny_data.get('gtin'):
            base_prompt += f"- GTIN/EAN: {tiny_data['gtin']}\n"

        base_prompt += "\n⚠️ IMPORTANTE: Use EXATAMENTE estas dimensões e peso nas descrições e cards. Não arredonde, não invente valores diferentes.\n"

    if has_files:
        # Adicionar instruções críticas sobre uso de arquivos
        base_prompt += "\n\n⚠️ INSTRUÇÕES CRÍTICAS SOBRE ARQUIVOS ENVIADOS:\n"
        base_prompt += "- Os arquivos anexados contêm informações REAIS e PRECISAS sobre o produto.\n"
        base_prompt += "- Para CARACTERÍSTICAS DO PRODUTO (dimensões, peso, materiais, especificações técnicas, cores, tamanhos, etc.):\n"
        base_prompt += "  → Use SOMENTE as informações EXPLICITAMENTE presentes nos arquivos enviados.\n"
        base_prompt += "  → NÃO invente, NÃO suponha, NÃO crie especificações que não estejam nos arquivos.\n"
        base_prompt += "  → Se uma especificação não estiver nos arquivos, NÃO a mencione.\n"
        base_prompt += "- Para COPY, MARKETING e TÉCNICAS DE VENDA:\n"
        base_prompt += "  → Use CRIATIVIDADE TOTAL para criar textos persuasivos e atraentes.\n"
        base_prompt += "  → Seja livre para usar técnicas de copywriting, gatilhos mentais e persuasão.\n"
        base_prompt += "  → Mas sempre baseado nas características REAIS extraídas dos arquivos.\n"

    return base_prompt


# -----------------------------------------------------------------------------
# API endpoints for configuration persistence
# -----------------------------------------------------------------------------

@app.get("/api/config")
async def get_config(
        current_user: CurrentUser = Depends(get_current_user_master),
        db: Session = Depends(get_db),
):
    user_id = str(current_user.user_id)

    cfg = db.query(UserConfig).filter(UserConfig.user_id == user_id).first()

    if not cfg:
        return JSONResponse(content=_default_config_payload())

    data = cfg.data or {}
    base = _default_config_payload()
    base.update(data)
    return JSONResponse(content=base)


@app.post("/api/config")
async def save_config(
        payload: ConfigPayload,
        current_user: CurrentUser = Depends(get_current_user_master),
        db: Session = Depends(get_db),
):
    user_id = str(current_user.user_id)

    cfg = db.query(UserConfig).filter(UserConfig.user_id == user_id).first()
    payload_dict = payload.model_dump()

    if cfg is None:
        cfg = UserConfig(user_id=user_id, data=payload_dict)
        db.add(cfg)
    else:
        cfg.data = payload_dict

    db.commit()
    db.refresh(cfg)

    base = _default_config_payload()
    base.update(cfg.data or {})
    return JSONResponse(content=base)


def _normalize_sku(sku: str) -> str:
    return str(sku or "").strip().upper()


def _normalize_marketplace(marketplace: Any) -> str:
    raw = str(marketplace or "").strip().lower()
    compact = re.sub(r"[^a-z0-9]", "", raw)
    aliases = {
        "mercadolivre": "mercadolivre",
        "mercadol": "mercadolivre",
        "meli": "mercadolivre",
        "ml": "mercadolivre",
        "shopee": "shopee",
        "amazon": "amazon",
        "magalu": "magalu",
        "shein": "shein",
    }
    if compact in aliases:
        return aliases[compact]
    return compact


def _normalize_kit_quantity(value: Any) -> int:
    try:
        qty = int(value)
    except (TypeError, ValueError):
        return 0
    return qty


def _kit_sku_candidates(base_sku: str, qty: int) -> List[str]:
    sku_norm = _normalize_sku(base_sku)
    quantity = _normalize_kit_quantity(qty)
    if not sku_norm or quantity < 2:
        return []
    return [f"{sku_norm}CB{quantity}", f"{sku_norm}-CB{quantity}"]


def _tiny_kit_resolution_to_payload(
    record: TinyKitResolution,
    *,
    from_cache: bool,
    message: str,
) -> Dict[str, Any]:
    validation = _to_safe_dict(record.validation_snapshot)
    return {
        "status": "found",
        "resolved_sku": record.resolved_sku,
        "searched_candidates": _kit_sku_candidates(record.sku_root_normalized, record.kit_quantity),
        "from_cache": bool(from_cache),
        "create_available": False,
        "validation": validation or None,
        "message": message,
    }


def _upsert_tiny_kit_resolution(
    db: Session,
    *,
    sku_root_normalized: str,
    kit_quantity: int,
    resolved_sku: str,
    validation_source: str,
    validation_snapshot: Optional[Dict[str, Any]] = None,
    unit_plural_override: Optional[str] = None,
    tiny_product_id: Optional[str] = None,
) -> TinyKitResolution:
    record = (
        db.query(TinyKitResolution)
        .filter(
            TinyKitResolution.sku_root_normalized == sku_root_normalized,
            TinyKitResolution.kit_quantity == kit_quantity,
        )
        .first()
    )
    now = datetime.utcnow()
    if record is None:
        record = TinyKitResolution(
            sku_root_normalized=sku_root_normalized,
            kit_quantity=kit_quantity,
            resolved_sku=resolved_sku,
            validation_source=validation_source or "pattern_skucb",
            unit_plural_override=unit_plural_override,
            tiny_product_id=tiny_product_id,
            validation_snapshot=_to_safe_dict(validation_snapshot),
            validated_at=now,
            last_checked_at=now,
        )
        db.add(record)
    else:
        record.resolved_sku = resolved_sku
        record.validation_source = validation_source or record.validation_source or "pattern_skucb"
        if unit_plural_override:
            record.unit_plural_override = unit_plural_override
        if tiny_product_id:
            record.tiny_product_id = tiny_product_id
        record.validation_snapshot = _to_safe_dict(validation_snapshot)
        record.validated_at = now
        record.last_checked_at = now
        record.updated_at = now
    db.flush()
    return record


def _to_safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _to_safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_index(value: Any, size: int, fallback_last: bool = False) -> int:
    try:
        idx = int(value)
    except (TypeError, ValueError):
        idx = -1

    if size <= 0:
        return -1
    if idx < 0:
        return size - 1 if fallback_last else -1
    if idx >= size:
        return size - 1
    return idx


def _hash_json(payload: Any) -> str:
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _normalize_text_block(raw: Any) -> Dict[str, Any]:
    raw_d = _to_safe_dict(raw)
    versions = [str(v) if v is not None else "" for v in _to_safe_list(raw_d.get("versions"))]
    idx = _coerce_index(raw_d.get("current_index"), len(versions), fallback_last=True)
    return {"versions": versions, "current_index": idx}


def _normalize_faq_line(raw: Any) -> Dict[str, Any]:
    raw_d = _to_safe_dict(raw)
    versions: List[Dict[str, str]] = []
    for item in _to_safe_list(raw_d.get("versions")):
        item_d = _to_safe_dict(item)
        versions.append({
            "q": str(item_d.get("q") or ""),
            "a": str(item_d.get("a") or "")
        })
    if not versions:
        versions = [{"q": "", "a": ""}]
    idx = _coerce_index(raw_d.get("current_index"), len(versions), fallback_last=True)
    return {
        "approved": bool(raw_d.get("approved", True)),
        "versions": versions,
        "current_index": idx
    }


def _normalize_card_line(raw: Any) -> Dict[str, Any]:
    raw_d = _to_safe_dict(raw)
    versions: List[Dict[str, str]] = []
    for item in _to_safe_list(raw_d.get("versions")):
        item_d = _to_safe_dict(item)
        versions.append({
            "title": str(item_d.get("title") or ""),
            "text": str(item_d.get("text") or "")
        })
    if not versions:
        versions = [{"title": "", "text": ""}]
    idx = _coerce_index(raw_d.get("current_index"), len(versions), fallback_last=True)
    return {"versions": versions, "current_index": idx}


def _normalize_metrics(raw: Any) -> Dict[str, float]:
    raw_d = _to_safe_dict(raw)
    return {
        "margin_percent": _coerce_float(raw_d.get("margin_percent"), 0.0),
        "value_multiple": _coerce_float(raw_d.get("value_multiple"), 0.0),
        "value_amount": _coerce_float(raw_d.get("value_amount"), 0.0),
    }


def _normalize_price_block(raw: Any) -> Dict[str, Any]:
    raw_d = _to_safe_dict(raw)
    versions: List[Dict[str, Any]] = []
    for item in _to_safe_list(raw_d.get("versions")):
        item_d = _to_safe_dict(item)
        versions.append({
            "price": _coerce_float(item_d.get("price"), 0.0),
            "metrics": _normalize_metrics(item_d.get("metrics")),
        })
    idx = _coerce_index(raw_d.get("current_index"), len(versions), fallback_last=True)
    return {"versions": versions, "current_index": idx}


_VARIANT_KEYS = ("simple", "kit2", "kit3", "kit4", "kit5")


def _empty_variant_state() -> Dict[str, Any]:
    return {
        "title": {"versions": [], "current_index": -1},
        "description": {"versions": [], "current_index": -1},
        "faq_lines": [],
        "card_lines": [],
    }


def _empty_versioned_state() -> Dict[str, Any]:
    return {
        "schema_version": 2,
        "variants": {key: _empty_variant_state() for key in _VARIANT_KEYS},
        # Precos sao volateis e recalculados no load; nao persistimos no DB.
        "prices": {},
    }


def _normalize_variant_state(raw: Any) -> Dict[str, Any]:
    raw_d = _to_safe_dict(raw)
    base = _empty_variant_state()
    base["title"] = _normalize_text_block(raw_d.get("title"))
    base["description"] = _normalize_text_block(raw_d.get("description"))
    base["faq_lines"] = [_normalize_faq_line(x) for x in _to_safe_list(raw_d.get("faq_lines"))]
    base["card_lines"] = [_normalize_card_line(x) for x in _to_safe_list(raw_d.get("card_lines"))]
    return base


def _normalize_versioned_state(raw: Any) -> Dict[str, Any]:
    raw_d = _to_safe_dict(raw)
    base = _empty_versioned_state()

    variants_raw = _to_safe_dict(raw_d.get("variants"))
    if variants_raw:
        for key in _VARIANT_KEYS:
            base["variants"][key] = _normalize_variant_state(variants_raw.get(key))
    else:
        # Compatibilidade retroativa V1: estado antigo vira a variante "simple".
        base["variants"]["simple"] = _normalize_variant_state(raw_d)

    # Ignora qualquer dado de preco vindo do cliente/DB.
    base["prices"] = {}
    return base


def _normalize_base_state(raw: Any, default_marketplace: str = "") -> Dict[str, Any]:
    raw_d = _to_safe_dict(raw)
    selected_marketplace = _normalize_marketplace(raw_d.get("selected_marketplace") or default_marketplace)
    return {
        "integration_mode": str(raw_d.get("integration_mode") or "manual"),
        "tiny_product_data": _to_safe_dict(raw_d.get("tiny_product_data")) or None,
        "selected_marketplace": selected_marketplace,
        "product_fields": _to_safe_dict(raw_d.get("product_fields")),
        "cost_price_cache": _to_safe_dict(raw_d.get("cost_price_cache")),
        "shipping_cost_cache": _to_safe_dict(raw_d.get("shipping_cost_cache")),
    }


def _merge_append_only_versions(current: List[Any], incoming: List[Any]) -> tuple[List[Any], int]:
    prefix = 0
    while prefix < len(current) and prefix < len(incoming) and current[prefix] == incoming[prefix]:
        prefix += 1
    merged = list(current)
    merged.extend(incoming[prefix:])
    return merged, prefix


def _merge_block_with_latest_index(current: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    cur_versions = _to_safe_list(current.get("versions"))
    inc_versions = _to_safe_list(incoming.get("versions"))

    merged_versions, prefix = _merge_append_only_versions(cur_versions, inc_versions)
    incoming_idx = _coerce_index(incoming.get("current_index"), len(inc_versions), fallback_last=False)
    current_idx = _coerce_index(current.get("current_index"), len(cur_versions), fallback_last=True)

    if incoming_idx >= 0:
        if incoming_idx < prefix:
            merged_idx = incoming_idx
        else:
            merged_idx = len(cur_versions) + (incoming_idx - prefix)
    else:
        merged_idx = current_idx

    merged_idx = _coerce_index(merged_idx, len(merged_versions), fallback_last=True)
    return {"versions": merged_versions, "current_index": merged_idx}


def _merge_lines(
    current_lines: List[Dict[str, Any]],
    incoming_lines: List[Dict[str, Any]],
    normalizer,
    preserve_approved: bool = False,
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    max_len = max(len(current_lines), len(incoming_lines))
    for idx in range(max_len):
        cur = normalizer(current_lines[idx]) if idx < len(current_lines) else None
        inc = normalizer(incoming_lines[idx]) if idx < len(incoming_lines) else None

        if cur is None and inc is not None:
            merged.append(inc)
            continue
        if inc is None and cur is not None:
            merged.append(cur)
            continue
        if cur is None and inc is None:
            continue

        merged_line = _merge_block_with_latest_index(cur, inc)
        if preserve_approved:
            merged_line["approved"] = bool(inc.get("approved", cur.get("approved", True)))
        merged.append(merged_line)
    return merged


def _merge_versioned_state(current: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    cur = _normalize_versioned_state(current)
    inc = _normalize_versioned_state(incoming)

    merged = _empty_versioned_state()
    for key in _VARIANT_KEYS:
        cur_variant = _normalize_variant_state(cur["variants"].get(key))
        inc_variant = _normalize_variant_state(inc["variants"].get(key))
        merged["variants"][key] = {
            "title": _merge_block_with_latest_index(cur_variant["title"], inc_variant["title"]),
            "description": _merge_block_with_latest_index(cur_variant["description"], inc_variant["description"]),
            "faq_lines": _merge_lines(
                cur_variant["faq_lines"],
                inc_variant["faq_lines"],
                normalizer=_normalize_faq_line,
                preserve_approved=True,
            ),
            "card_lines": _merge_lines(
                cur_variant["card_lines"],
                inc_variant["card_lines"],
                normalizer=_normalize_card_line,
                preserve_approved=False,
            ),
        }
    # Precos sao derivados e nao participam de merge/persistencia.
    merged["prices"] = {}
    return merged


def _normalize_workspace_action(action: Any) -> str:
    normalized = str(action or "manual").strip().lower()
    return normalized or "manual"


def _manual_text_replace_actions() -> set[str]:
    return {
        "title_manual_edit_start",
        "title_manual_edit_typing",
        "title_manual_edit_cancel",
        "title_manual_edit_commit",
        "description_manual_edit_start",
        "description_manual_edit_typing",
        "description_manual_edit_cancel",
        "description_manual_edit_commit",
    }


def _transient_workspace_actions() -> set[str]:
    return {
        "title_manual_edit_start",
        "title_manual_edit_typing",
        "title_manual_edit_cancel",
        "description_manual_edit_start",
        "description_manual_edit_typing",
        "description_manual_edit_cancel",
    }


def _workspace_to_api(workspace: SkuWorkspace) -> Dict[str, Any]:
    return {
        "id": workspace.id,
        "sku": workspace.sku_display,
        "sku_normalized": workspace.sku_normalized,
        "marketplace": workspace.marketplace_normalized,
        "marketplace_normalized": workspace.marketplace_normalized,
        "base_state": workspace.base_state or {},
        "versioned_state": workspace.versioned_state_current or _empty_versioned_state(),
        "state_seq": int(workspace.state_seq or 0),
        "updated_at": workspace.updated_at.isoformat() if workspace.updated_at else None,
    }


def _append_workspace_history(
    db: Session,
    workspace: SkuWorkspace,
    action: str,
    created_by_user_id: str,
    versioned_state_snapshot: Dict[str, Any],
) -> SkuWorkspaceHistory:
    row = SkuWorkspaceHistory(
        workspace_id=workspace.id,
        seq=int(workspace.state_seq or 0),
        action=(action or "manual").strip() or "manual",
        created_by_user_id=created_by_user_id,
        versioned_state_snapshot=versioned_state_snapshot,
        snapshot_hash=_hash_json(versioned_state_snapshot),
    )
    db.add(row)
    return row


async def _fetch_tiny_or_http_error(token: str, sku: str) -> Dict[str, Any]:
    try:
        return await tiny_service.get_product_by_sku(token=token, sku=sku)
    except tiny_service.TinyAuthError as e:
        raise HTTPException(status_code=401, detail={"message": str(e), "type": "auth_error"})
    except tiny_service.TinyNotFoundError as e:
        raise HTTPException(status_code=404, detail={"message": str(e), "type": "not_found"})
    except tiny_service.TinyRateLimitError as e:
        raise HTTPException(status_code=429, detail={"message": str(e), "type": "rate_limit"})
    except tiny_service.TinyTimeoutError as e:
        raise HTTPException(status_code=408, detail={"message": str(e), "type": "timeout"})
    except tiny_service.TinyServiceError as e:
        raise HTTPException(status_code=502, detail={"message": str(e), "type": "upstream_error"})


class SkuWorkspaceLoadIn(BaseModel):
    sku: str
    marketplace: str
    tiny_token: Optional[str] = None


class SkuWorkspaceSaveIn(BaseModel):
    sku: str
    marketplace: str
    base_state: Dict[str, Any] = Field(default_factory=dict)
    versioned_state: Dict[str, Any] = Field(default_factory=dict)
    action: str = "manual"


@app.post("/api/sku/workspace/load")
async def sku_workspace_load(
    payload: SkuWorkspaceLoadIn,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db),
):
    sku_normalized = _normalize_sku(payload.sku)
    marketplace_normalized = _normalize_marketplace(payload.marketplace)
    if not sku_normalized:
        raise HTTPException(status_code=400, detail={"message": "SKU é obrigatório."})
    if not marketplace_normalized:
        raise HTTPException(status_code=400, detail={"message": "Marketplace é obrigatório."})

    user_id = str(current_user.user_id)
    workspace = (
        db.query(SkuWorkspace)
        .filter(
            SkuWorkspace.sku_normalized == sku_normalized,
            SkuWorkspace.marketplace_normalized == marketplace_normalized,
        )
        .first()
    )
    if workspace:
        logger.info(
            "Workspace load DB hit | sku=%s | marketplace=%s | workspace_id=%s | user_id=%s",
            sku_normalized,
            marketplace_normalized,
            workspace.id,
            user_id,
        )
        workspace.last_accessed_at = datetime.utcnow()
        workspace.updated_by_user_id = user_id
        db.commit()
        db.refresh(workspace)
        return JSONResponse(content={"source": "db", "workspace": _workspace_to_api(workspace)})

    available_marketplaces = [
        row[0]
        for row in (
            db.query(SkuWorkspace.marketplace_normalized)
            .filter(SkuWorkspace.sku_normalized == sku_normalized)
            .all()
        )
        if row and row[0]
    ]
    logger.info(
        "Workspace load DB miss | sku=%s | marketplace=%s | user_id=%s | available_marketplaces=%s",
        sku_normalized,
        marketplace_normalized,
        user_id,
        ",".join(sorted(set(available_marketplaces))) if available_marketplaces else "(none)",
    )

    if not payload.tiny_token:
        raise HTTPException(
            status_code=400,
            detail={"message": "SKU não existe no DB para este marketplace e tiny_token não foi informado."},
        )

    product_data = await _fetch_tiny_or_http_error(payload.tiny_token, sku_normalized)
    base_state = _normalize_base_state(
        {
            "integration_mode": "tiny",
            "tiny_product_data": product_data,
            "selected_marketplace": marketplace_normalized,
        },
        default_marketplace=marketplace_normalized,
    )
    versioned_state = _empty_versioned_state()

    workspace = SkuWorkspace(
        sku_normalized=sku_normalized,
        marketplace_normalized=marketplace_normalized,
        sku_display=sku_normalized,
        base_state=base_state,
        versioned_state_current=versioned_state,
        state_seq=1,
        created_by_user_id=user_id,
        updated_by_user_id=user_id,
        last_accessed_at=datetime.utcnow(),
    )
    db.add(workspace)
    db.flush()
    history = _append_workspace_history(
        db=db,
        workspace=workspace,
        action="tiny_fetch",
        created_by_user_id=user_id,
        versioned_state_snapshot=versioned_state,
    )
    db.commit()
    db.refresh(workspace)
    db.refresh(history)

    return JSONResponse(
        content={
            "source": "tiny",
            "workspace": _workspace_to_api(workspace),
            "history_id": history.id,
        }
    )


@app.post("/api/sku/workspace/save")
async def sku_workspace_save(
    payload: SkuWorkspaceSaveIn,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db),
):
    sku_normalized = _normalize_sku(payload.sku)
    marketplace_normalized = _normalize_marketplace(payload.marketplace)
    if not sku_normalized:
        raise HTTPException(status_code=400, detail={"message": "SKU é obrigatório."})
    if not marketplace_normalized:
        raise HTTPException(status_code=400, detail={"message": "Marketplace é obrigatório."})

    user_id = str(current_user.user_id)
    action_name = _normalize_workspace_action(payload.action)
    text_replace_actions = _manual_text_replace_actions()
    transient_actions = _transient_workspace_actions()
    replace_mode = action_name in text_replace_actions
    transient_mode = action_name in transient_actions
    normalized_base = _normalize_base_state(payload.base_state, default_marketplace=marketplace_normalized)
    normalized_incoming = _normalize_versioned_state(payload.versioned_state)

    workspace = (
        db.query(SkuWorkspace)
        .filter(
            SkuWorkspace.sku_normalized == sku_normalized,
            SkuWorkspace.marketplace_normalized == marketplace_normalized,
        )
        .first()
    )
    history: Optional[SkuWorkspaceHistory] = None

    if workspace is None:
        workspace = SkuWorkspace(
            sku_normalized=sku_normalized,
            marketplace_normalized=marketplace_normalized,
            sku_display=sku_normalized,
            base_state=normalized_base,
            versioned_state_current=normalized_incoming,
            state_seq=1,
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
            last_accessed_at=datetime.utcnow(),
        )
        db.add(workspace)
        db.flush()
        history = _append_workspace_history(
            db=db,
            workspace=workspace,
            action=action_name,
            created_by_user_id=user_id,
            versioned_state_snapshot=normalized_incoming,
        )
        db.commit()
        db.refresh(workspace)
        db.refresh(history)
        return JSONResponse(
            content={
                "ok": True,
                "saved": True,
                "workspace_id": workspace.id,
                "history_id": history.id,
                "reason": None,
            }
        )

    current_versioned = _normalize_versioned_state(workspace.versioned_state_current or {})
    if replace_mode:
        # Edicao manual em andamento: snapshot de entrada representa o estado mais recente
        # do draft e nao deve gerar append-only a cada tecla.
        merged_versioned = normalized_incoming
    else:
        merged_versioned = _merge_versioned_state(current_versioned, normalized_incoming)

    base_changed = (
        _hash_json(_normalize_base_state(workspace.base_state or {}, default_marketplace=marketplace_normalized))
        != _hash_json(normalized_base)
    )
    versioned_changed = _hash_json(current_versioned) != _hash_json(merged_versioned)
    if not base_changed and not versioned_changed:
        return JSONResponse(
            content={
                "ok": True,
                "saved": False,
                "workspace_id": workspace.id,
                "history_id": None,
                "reason": "no_changes",
            }
        )

    workspace.base_state = normalized_base
    workspace.versioned_state_current = merged_versioned
    workspace.updated_by_user_id = user_id
    workspace.updated_at = datetime.utcnow()
    workspace.last_accessed_at = datetime.utcnow()

    history_id: Optional[str] = None
    if transient_mode:
        # Autosave transitório (edicao em andamento): atualiza estado atual sem criar
        # entrada definitiva de historico/seq.
        db.commit()
        db.refresh(workspace)
    else:
        workspace.state_seq = int(workspace.state_seq or 0) + 1
        history = _append_workspace_history(
            db=db,
            workspace=workspace,
            action=action_name,
            created_by_user_id=user_id,
            versioned_state_snapshot=merged_versioned,
        )
        db.commit()
        db.refresh(workspace)
        db.refresh(history)
        history_id = history.id

    return JSONResponse(
        content={
            "ok": True,
            "saved": True,
            "workspace_id": workspace.id,
            "history_id": history_id,
            "reason": "transient_autosave" if transient_mode else None,
        }
    )


@app.get("/api/sku/workspace/versions")
async def sku_workspace_versions(
    sku: str,
    marketplace: str,
    limit: int = 50,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db),
):
    _ = current_user
    sku_normalized = _normalize_sku(sku)
    marketplace_normalized = _normalize_marketplace(marketplace)
    if not sku_normalized:
        raise HTTPException(status_code=400, detail={"message": "SKU é obrigatório."})
    if not marketplace_normalized:
        raise HTTPException(status_code=400, detail={"message": "Marketplace é obrigatório."})

    workspace = (
        db.query(SkuWorkspace)
        .filter(
            SkuWorkspace.sku_normalized == sku_normalized,
            SkuWorkspace.marketplace_normalized == marketplace_normalized,
        )
        .first()
    )
    if not workspace:
        raise HTTPException(status_code=404, detail={"message": "SKU não encontrado."})

    safe_limit = max(1, min(int(limit or 50), 200))
    rows = (
        db.query(SkuWorkspaceHistory)
        .filter(SkuWorkspaceHistory.workspace_id == workspace.id)
        .order_by(SkuWorkspaceHistory.seq.desc(), SkuWorkspaceHistory.created_at.desc())
        .limit(safe_limit)
        .all()
    )

    current_seq = int(workspace.state_seq or 0)
    metadata = [
        {
            "history_id": row.id,
            "seq": int(row.seq or 0),
            "action": row.action,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "created_by": row.created_by_user_id,
            "is_current": int(row.seq or 0) == current_seq,
        }
        for row in rows
    ]
    return JSONResponse(
        content={
            "sku": workspace.sku_display,
            "marketplace": workspace.marketplace_normalized,
            "versions": metadata,
        }
    )


@app.get("/api/sku/workspace/version/{history_id}")
async def sku_workspace_version_detail(
    history_id: str,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db),
):
    _ = current_user
    row = db.query(SkuWorkspaceHistory).filter(SkuWorkspaceHistory.id == history_id).first()
    if not row:
        raise HTTPException(status_code=404, detail={"message": "Versão não encontrada."})

    workspace = db.query(SkuWorkspace).filter(SkuWorkspace.id == row.workspace_id).first()
    return JSONResponse(
        content={
            "history_id": row.id,
            "workspace_id": row.workspace_id,
            "sku": workspace.sku_display if workspace else None,
            "marketplace": workspace.marketplace_normalized if workspace else None,
            "seq": int(row.seq or 0),
            "action": row.action,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "created_by": row.created_by_user_id,
            "versioned_state": row.versioned_state_snapshot or _empty_versioned_state(),
        }
    )


@app.post("/api/generate")
async def generate(
        request: Request,
        json_data: Optional[str] = Form(None),
        files: List[UploadFile] = File(default=[]),
        current_user: CurrentUser = Depends(get_current_user_master)
):
    # Detectar se é FormData ou JSON
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        # FormData com possíveis arquivos
        if not json_data:
            return JSONResponse(content={"error": "Missing json_data in FormData"}, status_code=400)
        payload_dict = json.loads(json_data)
        payload = GenerateIn(**payload_dict)
    else:
        # JSON puro (backward compatibility)
        payload_dict = await request.json()
        payload = GenerateIn(**payload_dict)
        files = []  # Sem arquivos em JSON

    if not (have_openai(payload.options) or have_gemini(payload.options)):
        return JSONResponse(content=mock_generate(payload.product_name, payload.marketplace))

    # Processar arquivos se houver
    files_data = None
    file_warnings = []
    has_files = len(files) > 0
    if has_files:
        files_data, file_warnings = await process_uploaded_files(files)
        # Se todos os arquivos foram rejeitados, tratar como sem arquivos
        if not files_data:
            has_files = False

    # Construir prompt com instruções específicas sobre arquivos
    base_prompt = build_full_prompt_with_files(payload.product_name, payload.marketplace, payload.options, has_files)
    data = call_model_json(base_prompt, payload.options, files_data)

    title = str(data.get("title", "")).strip()
    description = ensure_plain_text_desc(str(data.get("description", "")))
    faq = data.get("faq") or []
    cards = data.get("cards") or []

    response_data = {
        "title": title,
        "description": description,
        "faq": faq,
        "cards": cards,
        "sources_used": {"mock": False}
    }

    # Adicionar avisos sobre arquivos se houver
    if file_warnings:
        response_data["file_warnings"] = file_warnings

    if has_files and files_data:
        response_data["files_processed"] = len(files_data)

    return JSONResponse(content=response_data)


@app.post("/api/regen")
async def regen(payload: RegenIn, current_user: CurrentUser = Depends(get_current_user_master)):
    field = payload.field.lower().strip()

    if not (have_openai(payload.options) or have_gemini(payload.options)):
        if field == "title":
            t = f"{payload.product_name} — {random.choice(['Qualidade superior', 'Uso prático diário', 'Resistência e design'])}"
            return JSONResponse(content={"title": t, "sources_used": {"mock": True}})
        if field == "description":
            base = mock_generate(payload.product_name, payload.marketplace)
            return JSONResponse(content={"description": base["description"], "sources_used": {"mock": True}})
        if field == "faq_item":
            item = random.choice(mock_faq())
            return JSONResponse(content={"faq": [item], "sources_used": {"mock": True}})
        if field == "card":
            item = random.choice(mock_cards(payload.product_name))
            return JSONResponse(content={"cards": [item], "sources_used": {"mock": True}})
        return JSONResponse(content={"ok": True, "sources_used": {"mock": True}})

    base_prompt = build_full_prompt(payload.product_name, payload.marketplace, payload.options)
    prev = payload.context.get("previous") if payload.context else None
    part_prompt = build_field_prompt(base_prompt, field, previous=prev, user_hint=payload.prompt)
    data = call_model_json(part_prompt, payload.options)

    out: Dict[str, Any] = {"sources_used": {"mock": False}}
    if field == "title" and "title" in data:
        out["title"] = str(data["title"]).strip()
    elif field == "description" and "description" in data:
        out["description"] = ensure_plain_text_desc(str(data["description"]))
    elif field == "faq_item" and "faq" in data:
        try:
            item = data["faq"][0]
        except Exception:
            item = {}
        out["faq"] = [item]
    elif field == "card" and "cards" in data:
        try:
            item = data["cards"][0]
        except Exception:
            item = {}
        out["cards"] = [item]
    return JSONResponse(content=out)


# ===== Tiny ERP Integration Endpoints =====

class TinyGetProductIn(BaseModel):
    """Request para buscar produto do Tiny por SKU"""
    token: str = Field(..., description="Token API do Tiny ERP")
    sku: str = Field(..., description="SKU do produto a buscar")


class TinyValidateTokenIn(BaseModel):
    """Request para validar token do Tiny"""
    token: str = Field(..., description="Token API do Tiny ERP para validar")


class TinyResolveKitIn(BaseModel):
    token: str = Field(..., description="Token API do Tiny ERP")
    base_sku: str = Field(..., description="SKU raiz (anuncio simples)")
    kit_quantity: int = Field(..., description="Quantidade do kit (2..5)")
    force_refresh: bool = Field(False, description="Ignora cache global e reconsulta Tiny")


class TinyCreateKitIn(BaseModel):
    token: str = Field(..., description="Token API do Tiny ERP")
    base_sku: str = Field(..., description="SKU raiz (anuncio simples)")
    kit_quantity: int = Field(..., description="Quantidade do kit (2..5)")
    unit_plural_override: Optional[str] = Field(None, description="Unidade no plural para nome do combo")
    combo_name_override: Optional[str] = Field(None, description="Nome final do combo a ser cadastrado no Tiny")
    announcement_price: Optional[float] = Field(None, description="Preco do anuncio da aba ativa do kit")
    promotional_price: Optional[float] = Field(0.0, description="Preco promocional para cadastro no Tiny")
    base_unit_override: Optional[str] = Field(None, description="Unidade do produto simples para reutilizar no KIT")
    kit_weight_kg: Optional[float] = Field(None, description="Peso liquido/bruto do kit")
    kit_height_cm: Optional[float] = Field(None, description="Altura do kit")
    kit_width_cm: Optional[float] = Field(None, description="Largura do kit")
    kit_length_cm: Optional[float] = Field(None, description="Comprimento do kit")
    kit_volumes: Optional[int] = Field(1, description="Numero de volumes do kit")
    kit_description: Optional[str] = Field(None, description="Descricao complementar do kit")


class TinySuggestKitNameIn(BaseModel):
    token: str = Field(..., description="Token API do Tiny ERP")
    base_sku: str = Field(..., description="SKU raiz (anuncio simples)")
    kit_quantity: int = Field(..., description="Quantidade do kit (2..5)")
    unit_plural_override: Optional[str] = Field(None, description="Unidade no plural para sugestao de nome")


@app.post("/api/tiny/product")
async def tiny_get_product(request: TinyGetProductIn, current_user: CurrentUser = Depends(get_current_user_master)):
    """
    Busca dados de um produto no Tiny ERP por SKU.
    
    Retorna:
        - 200: Produto encontrado com sucesso
        - 401: Token inválido
        - 404: SKU não encontrado
        - 408: Timeout
        - 500: Erro interno
    """
    try:
        product_data = await tiny_service.get_product_by_sku(
            token=request.token,
            sku=request.sku
        )

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "data": product_data
            }
        )

    except tiny_service.TinyAuthError as e:
        raise HTTPException(
            status_code=401,
            detail={
                "status": "error",
                "type": "auth_error",
                "message": str(e)
            }
        )

    except tiny_service.TinyNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail={
                "status": "error",
                "type": "not_found",
                "message": str(e)
            }
        )

    except tiny_service.TinyRateLimitError as e:
        raise HTTPException(
            status_code=429,
            detail={
                "status": "error",
                "type": "rate_limit",
                "message": str(e)
            }
        )

    except tiny_service.TinyTimeoutError as e:
        raise HTTPException(
            status_code=408,
            detail={
                "status": "error",
                "type": "timeout",
                "message": str(e)
            }
        )

    except tiny_service.TinyServiceError as e:
        raise HTTPException(
            status_code=502,
            detail={
                "status": "error",
                "type": "upstream_error",
                "message": str(e)
            }
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "type": "internal_error",
                "message": f"Erro ao buscar produto: {str(e)}"
            }
        )


@app.post("/api/tiny/validate-token")
async def tiny_validate_token(
        request: TinyValidateTokenIn,
        current_user: CurrentUser = Depends(get_current_user_master)
):
    """
    Valida um token do Tiny ERP.
    
    Retorna:
        - 200: Token validado (válido ou inválido)
    """
    try:
        is_valid, error_message = await tiny_service.validate_token(request.token)

        return JSONResponse(
            status_code=200,
            content={
                "valid": is_valid,
                "message": error_message if not is_valid else "Token válido"
            }
        )

    except Exception as e:
        return JSONResponse(
            status_code=200,
            content={
                "valid": False,
                "message": f"Erro ao validar: {str(e)}"
            }
        )


@app.post("/api/tiny/kit/resolve")
async def tiny_resolve_kit(
    payload: TinyResolveKitIn,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db),
):
    _ = current_user
    sku_root = _normalize_sku(payload.base_sku)
    kit_quantity = _normalize_kit_quantity(payload.kit_quantity)
    if not sku_root:
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "type": "base_sku_required", "message": "SKU base obrigatorio."},
        )
    if kit_quantity < 2 or kit_quantity > 5:
        raise HTTPException(
            status_code=422,
            detail={"status": "error", "type": "kit_quantity_invalid", "message": "Quantidade de kit invalida (2..5)."},
        )

    if not payload.force_refresh:
        cached = (
            db.query(TinyKitResolution)
            .filter(
                TinyKitResolution.sku_root_normalized == sku_root,
                TinyKitResolution.kit_quantity == kit_quantity,
            )
            .first()
        )
        if cached:
            cached.last_checked_at = datetime.utcnow()
            db.commit()
            db.refresh(cached)
            return JSONResponse(
                content=_tiny_kit_resolution_to_payload(
                    cached,
                    from_cache=True,
                    message=f"SKU de kit recuperado do cache global: {cached.resolved_sku}",
                )
            )

    try:
        result = await tiny_service.resolve_kit_candidate(
            token=payload.token,
            base_sku=sku_root,
            kit_quantity=kit_quantity,
        )
        if result.get("status") == "found":
            record = _upsert_tiny_kit_resolution(
                db,
                sku_root_normalized=sku_root,
                kit_quantity=kit_quantity,
                resolved_sku=str(result.get("resolved_sku") or ""),
                validation_source="pattern_skucb"
                if str(result.get("resolved_sku") or "").upper() == f"{sku_root}CB{kit_quantity}"
                else "pattern_sku_dash_cb",
                validation_snapshot=_to_safe_dict(result.get("validation")),
            )
            db.commit()
            db.refresh(record)
            response = _tiny_kit_resolution_to_payload(
                record,
                from_cache=False,
                message=str(result.get("message") or f"Kit valido encontrado: {record.resolved_sku}"),
            )
            response["searched_candidates"] = result.get("searched_candidates") or response["searched_candidates"]
            return JSONResponse(content=response)

        return JSONResponse(
            content={
                "status": "missing",
                "resolved_sku": None,
                "searched_candidates": result.get("searched_candidates") or _kit_sku_candidates(sku_root, kit_quantity),
                "from_cache": False,
                "create_available": True,
                "validation": result.get("validation"),
                "message": result.get("message") or "Nenhum kit valido encontrado.",
            }
        )
    except tiny_service.TinyAuthError as e:
        raise HTTPException(status_code=401, detail={"status": "error", "type": "auth_error", "message": str(e)})
    except tiny_service.TinyNotFoundError as e:
        raise HTTPException(status_code=404, detail={"status": "error", "type": "not_found", "message": str(e)})
    except tiny_service.TinyValidationError as e:
        raise HTTPException(status_code=422, detail={"status": "error", "type": e.code, "message": str(e)})
    except tiny_service.TinyRateLimitError as e:
        raise HTTPException(status_code=429, detail={"status": "error", "type": "rate_limit", "message": str(e)})
    except tiny_service.TinyTimeoutError as e:
        raise HTTPException(status_code=408, detail={"status": "error", "type": "timeout", "message": str(e)})
    except tiny_service.TinyServiceError as e:
        raise HTTPException(status_code=502, detail={"status": "error", "type": "upstream_error", "message": str(e)})


@app.post("/api/tiny/kit/create")
async def tiny_create_kit(
    payload: TinyCreateKitIn,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db),
):
    user_id = str(current_user.user_id)
    sku_root = _normalize_sku(payload.base_sku)
    kit_quantity = _normalize_kit_quantity(payload.kit_quantity)
    if not sku_root:
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "type": "base_sku_required", "message": "SKU base obrigatorio."},
        )
    if kit_quantity < 2 or kit_quantity > 5:
        raise HTTPException(
            status_code=422,
            detail={"status": "error", "type": "kit_quantity_invalid", "message": "Quantidade de kit invalida (2..5)."},
        )

    cached = (
        db.query(TinyKitResolution)
        .filter(
            TinyKitResolution.sku_root_normalized == sku_root,
            TinyKitResolution.kit_quantity == kit_quantity,
        )
        .first()
    )
    if cached:
        cached.last_checked_at = datetime.utcnow()
        db.commit()
        db.refresh(cached)
        return JSONResponse(
            content={
                "status": "already_exists",
                "resolved_sku": cached.resolved_sku,
                "tiny_product_id": cached.tiny_product_id,
                "validation": _to_safe_dict(cached.validation_snapshot) or None,
                "message": f"SKU de kit ja validado no cache global: {cached.resolved_sku}",
            }
        )

    cfg = db.query(UserConfig).filter(UserConfig.user_id == user_id).first()
    user_config_data = cfg.data if cfg and isinstance(cfg.data, dict) else {}
    kit_name_replacements = _extract_kit_name_replacements_from_config(user_config_data)

    try:
        created = await tiny_service.create_kit_product(
            token=payload.token,
            base_sku=sku_root,
            kit_quantity=kit_quantity,
            unit_plural_override=payload.unit_plural_override,
            combo_name_override=payload.combo_name_override,
            kit_name_replacements=kit_name_replacements,
            announcement_price=payload.announcement_price,
            promotional_price=payload.promotional_price,
            base_unit_override=payload.base_unit_override,
            kit_weight_kg=payload.kit_weight_kg,
            kit_height_cm=payload.kit_height_cm,
            kit_width_cm=payload.kit_width_cm,
            kit_length_cm=payload.kit_length_cm,
            kit_volumes=payload.kit_volumes,
            kit_description=payload.kit_description,
        )
        record = _upsert_tiny_kit_resolution(
            db,
            sku_root_normalized=sku_root,
            kit_quantity=kit_quantity,
            resolved_sku=str(created.get("resolved_sku") or f"{sku_root}CB{kit_quantity}"),
            validation_source="auto_create",
            validation_snapshot=_to_safe_dict(created.get("validation")),
            unit_plural_override=str(created.get("unit_plural") or payload.unit_plural_override or "").strip().upper() or None,
            tiny_product_id=str(created.get("tiny_product_id") or "").strip() or None,
        )
        db.commit()
        db.refresh(record)
        return JSONResponse(
            content={
                "status": "created",
                "resolved_sku": record.resolved_sku,
                "tiny_product_id": record.tiny_product_id,
                "validation": _to_safe_dict(record.validation_snapshot) or None,
                "message": f"KIT cadastrado com sucesso no Tiny: {record.resolved_sku}",
            }
        )
    except tiny_service.TinyConflictError:
        conflict_sku = f"{sku_root}CB{kit_quantity}"
        raise HTTPException(
            status_code=409,
            detail={
                "status": "error",
                "type": "kit_sku_collision",
                "message": f"Nao foi possivel cadastrar o KIT automaticamente: o codigo {conflict_sku} ja existe no Tiny.",
            },
        )
    except tiny_service.TinyAuthError as e:
        raise HTTPException(status_code=401, detail={"status": "error", "type": "auth_error", "message": str(e)})
    except tiny_service.TinyNotFoundError as e:
        raise HTTPException(status_code=404, detail={"status": "error", "type": "not_found", "message": str(e)})
    except tiny_service.TinyValidationError as e:
        raise HTTPException(status_code=422, detail={"status": "error", "type": e.code, "message": str(e)})
    except tiny_service.TinyRateLimitError as e:
        raise HTTPException(status_code=429, detail={"status": "error", "type": "rate_limit", "message": str(e)})
    except tiny_service.TinyTimeoutError as e:
        raise HTTPException(status_code=408, detail={"status": "error", "type": "timeout", "message": str(e)})
    except tiny_service.TinyServiceError as e:
        raise HTTPException(status_code=502, detail={"status": "error", "type": "upstream_error", "message": str(e)})


@app.post("/api/tiny/kit/suggest-name")
async def tiny_suggest_kit_name(
    payload: TinySuggestKitNameIn,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db),
):
    user_id = str(current_user.user_id)
    sku_root = _normalize_sku(payload.base_sku)
    kit_quantity = _normalize_kit_quantity(payload.kit_quantity)
    if not sku_root:
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "type": "base_sku_required", "message": "SKU base obrigatorio."},
        )
    if kit_quantity < 2 or kit_quantity > 5:
        raise HTTPException(
            status_code=422,
            detail={"status": "error", "type": "kit_quantity_invalid", "message": "Quantidade de kit invalida (2..5)."},
        )

    cfg = db.query(UserConfig).filter(UserConfig.user_id == user_id).first()
    user_config_data = cfg.data if cfg and isinstance(cfg.data, dict) else {}
    kit_name_replacements = _extract_kit_name_replacements_from_config(user_config_data)

    try:
        suggestion = await tiny_service.suggest_kit_name(
            token=payload.token,
            base_sku=sku_root,
            kit_quantity=kit_quantity,
            unit_plural_override=payload.unit_plural_override,
            kit_name_replacements=kit_name_replacements,
        )
        return JSONResponse(
            content={
                "status": "ok",
                "combo_name": str(suggestion.get("combo_name") or ""),
                "unit_plural": str(suggestion.get("unit_plural") or ""),
            }
        )
    except tiny_service.TinyAuthError as e:
        raise HTTPException(status_code=401, detail={"status": "error", "type": "auth_error", "message": str(e)})
    except tiny_service.TinyNotFoundError as e:
        raise HTTPException(status_code=404, detail={"status": "error", "type": "not_found", "message": str(e)})
    except tiny_service.TinyValidationError as e:
        raise HTTPException(status_code=422, detail={"status": "error", "type": e.code, "message": str(e)})
    except tiny_service.TinyRateLimitError as e:
        raise HTTPException(status_code=429, detail={"status": "error", "type": "rate_limit", "message": str(e)})
    except tiny_service.TinyTimeoutError as e:
        raise HTTPException(status_code=408, detail={"status": "error", "type": "timeout", "message": str(e)})
    except tiny_service.TinyServiceError as e:
        raise HTTPException(status_code=502, detail={"status": "error", "type": "upstream_error", "message": str(e)})


# ============================================================================
# PRICING ENDPOINTS
# ============================================================================

class PriceQuoteRequest(BaseModel):
    """Request para cotação de preços"""
    cost_price: float = Field(..., gt=0, description="Custo do produto (deve ser > 0)")
    shipping_cost: float = Field(0.0, ge=0, description="Custo de frete/envio (padrão 0.0)")
    channel: str = Field(..., description="Canal de venda (mercadolivre, shopee, amazon, etc)")
    commission_percent: Optional[float] = Field(None, ge=0, le=1,
                                                description="Percentual de comissão direto (0.0 a 1.0, ex: 0.15 = 15%)")
    policy_id: Optional[str] = Field(None, description="ID da política de preços (opcional)")
    ctx: Optional[Dict[str, Any]] = Field(None, description="Contexto adicional (categoria, região, etc)")


class PriceQuoteResponse(BaseModel):
    """Resposta da cotação de preços com métricas"""
    listing_price: Dict[str, Any]  # {price, metrics}
    wholesale_tiers: List[Dict[str, Any]]  # [{tier, min_quantity, price, metrics}]
    aggressive_price: Dict[str, Any]  # {price, metrics}
    promo_price: Dict[str, Any]  # {price, metrics}
    breakdown: Dict[str, Any]
    channel: str
    policy_id: Optional[str] = None


class PriceValidateRequest(BaseModel):
    """Request para validação de entrada"""
    cost_price: float
    shipping_cost: float = 0.0
    channel: str


@app.post("/pricing/quote", response_model=PriceQuoteResponse)
async def pricing_quote(
        request: PriceQuoteRequest,
        current_user: CurrentUser = Depends(get_current_user_master)
):
    """
    Calcula todos os preços derivados a partir do custo e canal COM MÉTRICAS.

    Args:
        request: PriceQuoteRequest com cost_price, channel, policy_id?, ctx?
        current_user: SSO validation mechanism

    Returns:
        PriceQuoteResponse com todos os preços calculados, métricas e breakdown

    Raises:
        422: Canal não suportado ou cost_price inválido
    """
    try:
        # Obter calculadora para o canal
        calculator = PriceCalculatorFactory.get(request.channel)

        # Preparar contexto: adicionar commission_percent se fornecido
        ctx = request.ctx or {}
        if request.commission_percent is not None:
            ctx['commission_percent'] = request.commission_percent

        # Calcular todos os preços COM MÉTRICAS (incluindo shipping_cost)
        listing_price_obj = calculator.get_listing_price_with_metrics(request.cost_price, request.shipping_cost, ctx)
        wholesale_tiers = calculator.get_wholesale_tiers_with_metrics(request.cost_price, request.shipping_cost, ctx)
        aggressive_price_obj = calculator.get_aggressive_price_with_metrics(request.cost_price, request.shipping_cost,
                                                                            ctx)
        promo_price_obj = calculator.get_promo_price_with_metrics(request.cost_price, request.shipping_cost, ctx)
        breakdown = calculator.get_breakdown(request.cost_price, request.shipping_cost, ctx)

        # Converter tiers para dict
        tiers_dict = [tier.model_dump() for tier in wholesale_tiers]

        return PriceQuoteResponse(
            listing_price=listing_price_obj.model_dump(),
            wholesale_tiers=tiers_dict,
            aggressive_price=aggressive_price_obj.model_dump(),
            promo_price=promo_price_obj.model_dump(),
            breakdown=breakdown.model_dump(),
            channel=request.channel,
            policy_id=request.policy_id
        )

    except ValueError as e:
        # Canal não suportado
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(e),
                "supported_channels": PriceCalculatorFactory.get_supported_channels()
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"message": f"Erro ao calcular preços: {str(e)}"}
        )


class MLShippingRequest(BaseModel):
    cost_price: float
    weight_kg: float
    reference_price: Optional[float] = None


@app.post("/api/shipping/calculate_ml")
async def calculate_ml_shipping_endpoint(
        request: MLShippingRequest,
        current_user: CurrentUser = Depends(get_current_user_master)
):
    try:
        layout_ok = await ml_shipping.is_shipping_layout_valid()
        if not layout_ok:
            raise HTTPException(
                status_code=502,
                detail="Os dados de fretes do Mercado Livre estão inconsistentes no momento. Tente novamente mais tarde."
            )
        val = await ml_shipping.get_shipping_cost(request.cost_price, request.weight_kg, request.reference_price)
        return JSONResponse(content={"shipping_cost": val})
    except ml_shipping.MLShippingError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/pricing/policies")
async def pricing_policies(current_user: CurrentUser = Depends(get_current_user_master)):
    """
    Lista políticas de preço disponíveis por canal.
    
    Retorna:
        Dict com canais suportados e suas configurações padrão
    """
    supported_channels = PriceCalculatorFactory.get_supported_channels()

    policies = {}
    for channel in supported_channels:
        try:
            calculator = PriceCalculatorFactory.get(channel)
            # Acessa atributos diretamente via hasattr (compatível com todas as implementações)
            policies[channel] = {
                "default_markup": getattr(calculator, "DEFAULT_MARKUP", 2.0),
                "default_tax_rate": getattr(calculator, "DEFAULT_TAX_RATE", 0.15),
                "min_margin": getattr(calculator, "MIN_MARGIN", 0.20),
                "aggressive_discount": getattr(calculator, "AGGRESSIVE_DISCOUNT", 0.10),
                "promo_discount": getattr(calculator, "PROMO_DISCOUNT", 0.15),
            }
        except Exception:
            pass

    return {
        "supported_channels": supported_channels,
        "policies": policies
    }


@app.post("/pricing/validate")
async def pricing_validate(
        request: PriceValidateRequest,
        current_user: CurrentUser = Depends(get_current_user_master)
):
    """
    Valida entradas de precificação.
    
    Args:
        request: PriceValidateRequest com cost_price e channel
        current_user: SSO validation mechanism
        
    Returns:
        200: Válido
        422: Inválido (com mensagem de erro)
    """
    errors = []

    # Validar cost_price
    if request.cost_price <= 0:
        errors.append("cost_price deve ser maior que zero")

    # Validar shipping_cost
    if request.shipping_cost < 0:
        errors.append("shipping_cost não pode ser negativo")

    # Validar channel
    if not PriceCalculatorFactory.is_supported(request.channel):
        errors.append(
            f"Canal '{request.channel}' não suportado. "
            f"Canais disponíveis: {', '.join(PriceCalculatorFactory.get_supported_channels())}"
        )

    if errors:
        raise HTTPException(
            status_code=422,
            detail={"errors": errors}
        )

    return {"valid": True, "message": "Entrada válida"}


class CalcMetricsRequest(BaseModel):
    price: float
    cost_price: float
    shipping_cost: float = 0.0
    channel: str
    ctx: Optional[
        Dict[str, Any]] = None  # deve aceitar commission_percent, impostos, tacos, margem_contribuicao, lucro, etc.


class CalcMetricsResponse(BaseModel):
    margin_percent: float
    value_multiple: float
    value_amount: float


@app.post("/pricing/calculate-metrics", response_model=CalcMetricsResponse)
async def pricing_calculate_metrics(
        request: CalcMetricsRequest,
        current_user: CurrentUser = Depends(get_current_user_master)
):
    """
    Calcula métricas (margin_percent, value_multiple, value_amount) para um PREÇO informado,
    considerando cost_price, shipping_cost e o contexto do canal.
    Tenta usar calculator.calculate_metrics(...); se não existir, faz um cálculo genérico com ctx.
    """
    try:
        calculator = PriceCalculatorFactory.get(request.channel)

        ctx = request.ctx or {}

        # Caminho 1: se a calculadora já expõe a função "calculate_metrics", use-a.
        if hasattr(calculator, "calculate_metrics"):
            metrics = calculator.calculate_metrics(
                price=request.price,
                cost_price=request.cost_price,
                shipping_cost=request.shipping_cost,
                ctx=ctx
            )
            # compatível com pydantic/model_dump ou dict simples
            if hasattr(metrics, "model_dump"):
                m = metrics.model_dump()
            elif isinstance(metrics, dict):
                m = metrics
            else:
                # fallback defensivo
                m = {
                    "margin_percent": float(getattr(metrics, "margin_percent", 0.0)),
                    "value_multiple": float(getattr(metrics, "value_multiple", 0.0)),
                    "value_amount": float(getattr(metrics, "value_amount", 0.0)),
                }
            return CalcMetricsResponse(**m)

        # Caminho 2 (fallback): cálculo genérico a partir do contexto (comissão, impostos, etc.)
        price = float(request.price or 0)
        cost_total = float(request.cost_price or 0) + float(request.shipping_cost or 0)

        commission_pct = float(ctx.get("commission_percent", 0.0) or 0.0)
        impostos_pct = float(ctx.get("impostos", 0.0) or 0.0)
        tacos_pct = float(ctx.get("tacos", 0.0) or 0.0)
        mc_pct = float(ctx.get("margem_contribuicao", 0.0) or 0.0)
        lucro_pct = float(ctx.get("lucro", 0.0) or 0.0)

        # Despesas proporcionais ao preço
        variaveis_sobre_preco = price * (commission_pct + impostos_pct + tacos_pct + mc_pct + lucro_pct)

        value_amount = price - cost_total - variaveis_sobre_preco
        margin_percent = (value_amount / price) * 100 if price > 0 else 0.0
        value_multiple = (value_amount / cost_total) if cost_total > 0 else 0.0

        return CalcMetricsResponse(
            margin_percent=margin_percent,
            value_multiple=value_multiple,
            value_amount=value_amount
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail={"message": f"Erro ao calcular métricas: {str(e)}"})


# ========= FUNÇÕES DE ITERAÇÃO COM O GATEWAY =========

@app.get("/auth/gateway-login")
async def gateway_login(request: Request, token: str, state: str = None, redirect: str = "/"):
    return await gateway_login_helper(request, token, state, redirect)


@app.get("/gateway_info", tags=["gateway"], summary="Informações para integração ao Application Gateway")
async def gateway_info():
    """
    Endpoint público que fornece ao Application Gateway
    as informações necessárias para auto-preenchimento da
    tela de cadastro de aplicativos internos.
    """

    data = {
        "name": "Ads Generator",
        "slug": settings.app_slug,
        "description": "Gerador de anúncios",
        "icon_url":
            "https://fraction-proxy-considers-coupon.trycloudflare.com/static/favicon.svg"
            if settings.dev_mode
            else "https://ads-generator.rapidopracachorro.com/static/favicon.svg",
        "tooltip": "Gerador de anúncios",
        "app_url":
            "https://fraction-proxy-considers-coupon.trycloudflare.com/auth/gateway-login"
            if settings.dev_mode
            else "https://ads-generator.rapidopracachorro.com/auth/gateway-login",
        "auth_type": "GATEWAY_TOKEN",
        "button_color": "#161824"
    }

    return JSONResponse(content=data)


# ============================================================================
# IMAGE SEARCH & GOOGLE DRIVE ENDPOINTS
# ============================================================================

from io import BytesIO
import json as _json
import PIL.Image

try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
    from google.oauth2 import service_account
    GOOGLE_DRIVE_AVAILABLE = True
except ImportError:
    GOOGLE_DRIVE_AVAILABLE = False
    HttpError = Exception


def _build_drive_service(credentials_json_str: str):
    """Build an authenticated Google Drive service from a service account JSON string."""
    if not GOOGLE_DRIVE_AVAILABLE:
        raise RuntimeError("google-api-python-client não está instalado.")
    creds_dict = _json.loads(credentials_json_str)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _escape_q(s: str) -> str:
    """Escapa aspas simples para queries do Google Drive."""
    return s.replace("'", "\\'")

def _get_or_create_subfolder(service, parent_folder_id: str, folder_name: str) -> str:
    """Get or create a subfolder inside parent_folder_id. Returns the subfolder ID."""
    safe_name = _escape_q(folder_name)
    query = (
        f"name='{safe_name}' and "
        f"'{parent_folder_id}' in parents and "
        "mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    results = service.files().list(
        q=query, 
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]

    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_folder_id]
    }
    folder = service.files().create(
        body=metadata, 
        fields="id",
        supportsAllDrives=True
    ).execute()
    return folder["id"]


def _find_file_in_folder(service, folder_id: str, filename: str) -> Optional[str]:
    """Find a file by name in a folder. Returns file ID or None."""
    safe_filename = _escape_q(filename)
    query = f"name='{safe_filename}' and '{folder_id}' in parents and trashed=false"
    results = service.files().list(
        q=query, 
        fields="files(id)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None


# ---- Validation Endpoints ----

class ValidateCredentialsIn(BaseModel):
    credentials_json: str


@app.post("/api/drive/validate-credentials")
async def validate_drive_credentials(
    payload: ValidateCredentialsIn,
    current_user: CurrentUser = Depends(get_current_user_master),
):
    if not GOOGLE_DRIVE_AVAILABLE:
        raise HTTPException(status_code=500, detail="Bibliotecas do Google Drive não instaladas no servidor.")
    try:
        service = _build_drive_service(payload.credentials_json)
        # Test: list 1 file to confirm access
        service.files().list(pageSize=1, fields="files(id)").execute()
        return JSONResponse(content={"valid": True, "message": "Credenciais válidas."})
    except _json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="JSON de credenciais inválido. Verifique o formato.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Credenciais inválidas: {str(e)}")


class ValidateFolderIn(BaseModel):
    credentials_json: str
    folder_id: str


@app.post("/api/drive/validate-folder")
async def validate_drive_folder(
    payload: ValidateFolderIn,
    current_user: CurrentUser = Depends(get_current_user_master),
):
    if not GOOGLE_DRIVE_AVAILABLE:
        raise HTTPException(status_code=500, detail="Bibliotecas do Google Drive não instaladas no servidor.")
    try:
        service = _build_drive_service(payload.credentials_json)
        result = service.files().get(
            fileId=payload.folder_id,
            fields="id, name, mimeType",
            supportsAllDrives=True
        ).execute()
        
        # MimeType de pasta comum ou de drive compartilhado
        is_folder = result.get("mimeType") == "application/vnd.google-apps.folder"
        
        if not is_folder:
            raise HTTPException(status_code=400, detail="O ID fornecido não é uma pasta válida ou Drive Compartilhado.")
            
        return JSONResponse(content={
            "valid": True,
            "folder_name": result.get("name"),
            "message": f"Pasta encontrada: {result.get('name')}"
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Pasta não encontrada ou sem acesso: {str(e)}")


class ValidateImageSearchIn(BaseModel):
    api_key: str


@app.post("/api/images/validate-search")
async def validate_image_search_config(
        payload: ValidateImageSearchIn,
        current_user: CurrentUser = Depends(get_current_user_master),
):
    """Testa se a API Key do Serper.dev está correta e ativa."""
    url = "https://google.serper.dev/images"
    headers = {
        'X-API-KEY': payload.api_key,
        'Content-Type': 'application/json'
    }
    data = {"q": "teste", "num": 1}

    try:
        r = requests.post(url, headers=headers, json=data, timeout=10)
        if r.status_code == 200:
            return JSONResponse(content={"valid": True, "message": "Conectado ao Serper com sucesso!"})
        else:
            error_msg = r.json().get("message", "Chave inválida ou erro no serviço")
            raise HTTPException(status_code=r.status_code, detail=f"Erro no Serper: {error_msg}")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Erro na conexão: {str(e)}")


# ---- Search Images ----

class SearchImagesIn(BaseModel):
    query: str
    start: int = 1


@app.post("/api/images/search")
async def search_images(
    payload: SearchImagesIn,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db)
):
    user_id = str(current_user.user_id)
    cfg = db.query(UserConfig).filter(UserConfig.user_id == user_id).first()

    if not cfg or not cfg.data.get("image_search", {}).get("api_key"):
        raise HTTPException(status_code=400, detail="Serper API Key não configurada no Admin.")

    api_key = cfg.data["image_search"].get("api_key")

    url = "https://google.serper.dev/images"
    headers = {
        'X-API-KEY': api_key,
        'Content-Type': 'application/json'
    }
    # Calculando a página baseada no 'start' do frontend (1, 13, 25...)
    page = (payload.start // 12) + 1
    
    data = {
        "q": payload.query,
        "page": page,
        "num": 12
    }

    try:
        r = requests.post(url, headers=headers, json=data, timeout=10)
        r.raise_for_status()
        res_data = r.json()

        # O Serper retorna os resultados em 'images'
        items = res_data.get("images", [])
        images = [
            {
                "url": item.get("imageUrl"),
                "thumbnail": item.get("thumbnailUrl") or item.get("imageUrl"),
                "title": item.get("title"),
                "mime": None # Serper não retorna mime diretamente de forma fácil
            }
            for item in items
        ]

        return JSONResponse(content={
            "images": images,
            "total": 100 # Serper não envia total exato facilmente, fixamos um valor alto
        })
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Erro na API Serper: {str(e)}")


# ---- Save Images to Drive ----

class SaveToDriveIn(BaseModel):
    image_urls: List[str]
    product_name: str
    sku: Optional[str] = None


@app.post("/api/images/save-to-drive")
async def save_to_drive(
    payload: SaveToDriveIn,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db)
):
    if not GOOGLE_DRIVE_AVAILABLE:
        raise HTTPException(status_code=500, detail="Bibliotecas do Google Drive não instaladas no servidor.")

    user_id = str(current_user.user_id)
    cfg = db.query(UserConfig).filter(UserConfig.user_id == user_id).first()

    drive_cfg = cfg.data.get("google_drive", {}) if cfg else {}
    folder_id = drive_cfg.get("folder_id", "")
    credentials_json = drive_cfg.get("credentials_json", "")

    if not folder_id or not credentials_json:
        raise HTTPException(status_code=400, detail="Google Drive não configurado completamente no Admin.")

    try:
        service = _build_drive_service(credentials_json)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao autenticar no Drive: {str(e)}")

    # Use SKU if provided, otherwise fall back to sanitized product name
    folder_name = (payload.sku or payload.product_name).strip().replace("/", "-").replace("\\", "-")

    try:
        sku_folder_id = _get_or_create_subfolder(service, folder_id, folder_name)
        # Cria ou obtém a subpasta RAW_IMG dentro da pasta do SKU
        subfolder_id = _get_or_create_subfolder(service, sku_folder_id, "RAW_IMG")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao criar estrutura de pastas no Drive: {str(e)}")

    saved_count = 0
    errors = []

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    print(f"DEBUG: Iniciando salvamento de {len(payload.image_urls)} imagens na pasta '{folder_name}'")

    for idx, url in enumerate(payload.image_urls):
        filename = f"{folder_name}{idx + 1:03d}.png"
        try:
            # Download image with headers to avoid bot protection
            resp = requests.get(url, timeout=15, headers=headers)
            resp.raise_for_status()

            # Convert to PNG
            img = PIL.Image.open(BytesIO(resp.content)).convert("RGBA")
            out_buffer = BytesIO()
            img.save(out_buffer, format="PNG")
            out_buffer.seek(0)

            media = MediaIoBaseUpload(out_buffer, mimetype="image/png", resumable=False)

            # Check if file already exists → overwrite
            existing_id = _find_file_in_folder(service, subfolder_id, filename)
            if existing_id:
                service.files().update(
                    fileId=existing_id,
                    media_body=media,
                    supportsAllDrives=True
                ).execute()
                print(f"DEBUG: Arquivo atualizado: {filename}")
            else:
                service.files().create(
                    body={"name": filename, "parents": [subfolder_id]},
                    media_body=media,
                    fields="id",
                    supportsAllDrives=True
                ).execute()
                print(f"DEBUG: Arquivo criado: {filename}")

            saved_count += 1

        except Exception as e:
            error_msg = f"Erro na imagem {idx + 1} ({filename}): {str(e)}"
            print(f"ERROR: {error_msg}")
            errors.append(error_msg)

    print(f"DEBUG: Fim do processo. Salvas: {saved_count}, Erros: {len(errors)}")

    return JSONResponse(content={
        "status": "partial" if errors else "success",
        "saved": saved_count,
        "folder_name": folder_name,
        "errors": errors
    })


class LoadSkuFilesIn(BaseModel):
    sku: str

@app.post("/api/drive/load-sku-files")
async def load_sku_files(
    payload: LoadSkuFilesIn,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db)
):
    """
    Localiza a pasta com o nome do SKU no Drive, baixa todos os arquivos, 
    ordena ({SKU}001 primeiro) e retorna como base64 (Data URI).
    """
    if not GOOGLE_DRIVE_AVAILABLE:
        raise HTTPException(status_code=500, detail="Bibliotecas do Google Drive não instaladas no servidor.")

    user_id = str(current_user.user_id)
    cfg = db.query(UserConfig).filter(UserConfig.user_id == user_id).first()
    drive_cfg = cfg.data.get("google_drive", {}) if cfg else {}
    folder_id = drive_cfg.get("folder_id", "")
    credentials_json = drive_cfg.get("credentials_json", "")

    if not folder_id or not credentials_json:
        raise HTTPException(status_code=400, detail="Google Drive não configurado no Admin.")

    try:
        service = _build_drive_service(credentials_json)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao autenticar no Drive: {str(e)}")

    sku_nome = payload.sku.strip().replace('/', '-').replace('\\', '-')
    safe_name = _escape_q(sku_nome)
    
    # 1. Encontrar a pasta do SKU
    query = (
        f"name='{safe_name}' and "
        f"'{folder_id}' in parents and "
        "mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    
    try:
        results = service.files().list(
            q=query, 
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao buscar pasta: {str(e)}")
        
    folders = results.get("files", [])
    if not folders:
        raise HTTPException(status_code=404, detail=f"Pasta não encontrada para o SKU: {sku_nome}")
        
    subfolder_id = folders[0]["id"]
    
    # 2. Verificar se existe a subpasta RAW_IMG
    raw_img_folder_id = _find_file_in_folder(service, subfolder_id, "RAW_IMG")
    
    # Se existir RAW_IMG, carregamos de lá. Caso contrário, mantemos compatibility carregando da raiz da pasta SKU.
    target_folder_id = raw_img_folder_id if raw_img_folder_id else subfolder_id

    # 3. Listar imagens da pasta alvo (RAW_IMG ou raiz)
    query_imgs = f"'{target_folder_id}' in parents and trashed=false and mimeType!='application/vnd.google-apps.folder'"
    res_imgs = service.files().list(
        q=query_imgs,
        fields="files(id, name, mimeType, createdTime)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        pageSize=100
    ).execute()
    
    img_files = res_imgs.get("files", [])
    
    # Ordenar imagens: {SKU}NNN.ext primeiro de forma crescente
    # Suportando o padrão SKU[-_]?(CB\d+)?[-_]?\d+
    def sort_img_key(f):
        # Regex captura o número do combo (opcional) e o número da imagem (obrigatório)
        pattern = rf"^{re.escape(sku_nome)}[-_]?(?:CB(\d+))?[-_]?(\d+)\.[a-zA-Z0-9]+$"
        m = re.match(pattern, f["name"], re.IGNORECASE)
        if m:
            cb_num = int(m.group(1)) if m.group(1) else 0
            img_num = int(m.group(2))
            return (0, cb_num, img_num, f["name"])
        return (1, 0, 0, f["name"])
        
    img_files = sorted(img_files, key=sort_img_key)

    # 4. Listar arquivos da pasta RAW_KDB
    kdb_files = []
    raw_kdb_folder_id = _find_file_in_folder(service, subfolder_id, "RAW_KDB")
    if raw_kdb_folder_id:
        query_kdb = f"'{raw_kdb_folder_id}' in parents and trashed=false and mimeType!='application/vnd.google-apps.folder'"
        res_kdb = service.files().list(
            q=query_kdb,
            # Pedindo createdTime para ordenação
            fields="files(id, name, mimeType, createdTime)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageSize=100
        ).execute()
        kdb_files = res_kdb.get("files", [])
        
        # Ordenar KDB: do mais novo para o mais velho (createdTime desc)
        # createdTime no Drive é ISO 8601 string: "2023-10-27T10:00:00.000Z"
        kdb_files = sorted(kdb_files, key=lambda x: x.get("createdTime", ""), reverse=True)

    # 5. Combinar listas: Imagens primeiro, depois KDB
    all_files = img_files + kdb_files
    
    # 6. Baixar conteúdos
    out_files = []
    
    for f in all_files:
        try:
            req = service.files().get_media(fileId=f["id"])
            fh = BytesIO()
            downloader = MediaIoBaseDownload(fh, req)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            
            b64_content = base64.b64encode(fh.getvalue()).decode("utf-8")
            data_uri = f"data:{f['mimeType']};base64,{b64_content}"
            
            out_files.append({
                "name": f["name"],
                "type": f["mimeType"],
                "data_uri": data_uri
            })
        except Exception as e:
            print(f"ERROR: Falha ao baixar arquivo {f['name']} (ID: {f['id']}): {e}")
            continue
            
    return JSONResponse(content={"files": out_files})


# =============================================================================
# Canva Integration Endpoints
# =============================================================================

@app.get("/api/canva/auth")
async def canva_auth(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db)
):
    user_id = str(current_user.user_id)
    cfg = db.query(UserConfig).filter(UserConfig.user_id == user_id).first()
    if not cfg:
        return JSONResponse(
            status_code=400,
            content={"error": "Configuração não encontrada. Salve Client ID e Client Secret do Canva antes de autorizar."}
        )

    current_data = dict(cfg.data or {})
    canva_cfg = dict(current_data.get("canva", {}))
    client_id = canva_cfg.get("client_id")
    client_secret = canva_cfg.get("client_secret")
    if not client_id or not client_secret:
        return JSONResponse(
            status_code=400,
            content={"error": "Canva não configurado. Preencha e salve Client ID e Client Secret antes de autorizar."}
        )

    # Se estivermos atrás de um proxy, o FastAPI deve estar configurado para lidar, 
    # mas para garantir local ou produçao usamos request.base_url:
    base_url = str(request.base_url).rstrip('/')
    redirect_uri = f"{base_url}/api/canva/callback"
    
    # Gerar PKCE
    code_verifier, code_challenge = canva_service.generate_pkce()
    
    # Salvar code_verifier temporariamente no config do usuário.
    # Reatribui cfg.data inteiro para garantir persistência em coluna JSONB.
    canva_cfg["code_verifier"] = code_verifier
    current_data["canva"] = canva_cfg
    cfg.data = current_data
    db.commit()
    
    auth_url = canva_service.get_auth_url(client_id, redirect_uri, code_challenge, state=user_id)
    return RedirectResponse(url=auth_url)


@app.get("/api/canva/callback")
async def canva_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    db: Session = Depends(get_db)
):
    # Canva pode redirecionar com erro OAuth sem `code`.
    if error:
        return JSONResponse(
            status_code=400,
            content={
                "error": f"OAuth Canva retornou erro: {error}",
                "error_description": error_description or "Sem detalhes adicionais."
            }
        )

    if not code:
        return JSONResponse(
            status_code=400,
            content={
                "error": "Callback do Canva sem parâmetro `code`.",
                "hint": "Verifique se a autorização foi concluída no Canva e se o Redirect URI configurado é exatamente este endpoint."
            }
        )

    if not state:
        return JSONResponse(
            status_code=400,
            content={
                "error": "Callback do Canva sem parâmetro `state`."
            }
        )

    # State = user_id
    user_id = state
    cfg = db.query(UserConfig).filter(UserConfig.user_id == user_id).first()
    if not cfg:
        return JSONResponse(content={"error": "Usuário não encontrado."})
        
    canva_cfg = cfg.data.get("canva", {})
    client_id = canva_cfg.get("client_id")
    client_secret = canva_cfg.get("client_secret")
    code_verifier = canva_cfg.get("code_verifier")
    
    if not client_id or not client_secret or not code_verifier:
        missing = []
        if not client_id:
            missing.append("client_id")
        if not client_secret:
            missing.append("client_secret")
        if not code_verifier:
            missing.append("code_verifier")
        return JSONResponse(
            status_code=400,
            content={
                "error": "Canva não configurado ou sessão de auth inválida.",
                "missing": missing
            }
        )
        
    base_url = str(request.base_url).rstrip('/')
    redirect_uri = f"{base_url}/api/canva/callback"
    
    try:
        token_data = await canva_service.exchange_code(client_id, client_secret, code, redirect_uri, code_verifier)
    except canva_service.CanvaAuthError as e:
        return JSONResponse(content={"error": str(e)})
        
    # Salva os tokens no banco, incluindo metadata para refresh automatico.
    canva_cfg = _apply_canva_token_data(canva_cfg, token_data)
    canva_cfg.pop("code_verifier", None)

    current_data = dict(cfg.data or {})
    current_data["canva"] = canva_cfg
    cfg.data = current_data
    db.commit()
    
    success_url = "/?canva_auth=success"
    return HTMLResponse(
        content=f"""<!doctype html>
<html lang="pt-BR">
  <head>
    <meta charset="utf-8" />
    <title>Canva autorizado</title>
  </head>
  <body>
    <script>
      (function () {{
        var target = "{success_url}";
        var payload = {{
          type: "canva_oauth_result",
          status: "success",
          provider: "canva",
          at: Date.now()
        }};
        var origin = window.location.origin || "*";
        try {{
          if (window.opener && !window.opener.closed) {{
            window.opener.postMessage(payload, origin);
            window.close();
            return;
          }}
        }} catch (e) {{}}
        window.location.href = target;
      }})();
    </script>
    <p>Autorização concluída. Redirecionando...</p>
  </body>
</html>"""
    )


class CanvaSearchIn(BaseModel):
    sku: str

@app.post("/api/canva/list")
async def canva_list(
    payload: CanvaSearchIn,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db)
):
    user_id = str(current_user.user_id)
    try:
        access_token, _, _ = await _get_valid_canva_access_token(db, user_id)
        found = await _find_canva_design_with_cache(access_token, user_id, payload.sku)
    except canva_service.CanvaAuthError:
        access_token, _, _ = await _get_valid_canva_access_token(db, user_id, force_refresh=True)
        try:
            found = await _find_canva_design_with_cache(access_token, user_id, payload.sku)
        except canva_service.CanvaAuthError:
            raise HTTPException(status_code=401, detail=_canva_reauth_detail())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return JSONResponse(content={"design": found})


class CanvaExportIn(BaseModel):
    sku: str
    design_id: str


# Estado em memória para tarefas de exportação Canva -> Drive
CANVA_EXPORT_TASKS: Dict[str, Dict[str, Any]] = {}
CANVA_EXPORT_TASK_TTL_SECONDS = 3600

# Jobs de publicação ML em memória
ML_PUBLISH_JOBS: Dict[str, Any] = {}
ML_PUBLISH_JOB_TTL = 600  # 10 minutos


def _cleanup_ml_publish_jobs() -> None:
    now = time.time()
    expired = [k for k, v in ML_PUBLISH_JOBS.items() if now - v.get("created_at", 0) > ML_PUBLISH_JOB_TTL]
    for k in expired:
        del ML_PUBLISH_JOBS[k]


# Cache em memória de designs do Canva por usuário.
# Estratégia:
# - guarda todas as páginas já varridas
# - ao buscar SKU: primeiro consulta cache; se não achar e cache não completo, continua da próxima página
# - se não achar SKU, percorre até o fim e marca cache como completo
CANVA_DESIGN_CACHE: Dict[str, Dict[str, Any]] = {}
CANVA_DESIGN_CACHE_LOCKS: Dict[str, asyncio.Lock] = {}
CANVA_DESIGN_CACHE_IDLE_TTL_SECONDS = 24 * 3600


def _cleanup_canva_export_tasks():
    now = time.time()
    stale = []
    for task_id, task in CANVA_EXPORT_TASKS.items():
        status = task.get("status")
        updated_at = float(task.get("updated_at", now))
        if status in {"success", "error"} and (now - updated_at) > CANVA_EXPORT_TASK_TTL_SECONDS:
            stale.append(task_id)
    for task_id in stale:
        CANVA_EXPORT_TASKS.pop(task_id, None)


def _cleanup_canva_design_cache():
    now = time.time()
    stale_users = []
    for user_id, entry in CANVA_DESIGN_CACHE.items():
        updated_at = float(entry.get("updated_at", now))
        if (now - updated_at) > CANVA_DESIGN_CACHE_IDLE_TTL_SECONDS:
            stale_users.append(user_id)

    for user_id in stale_users:
        CANVA_DESIGN_CACHE.pop(user_id, None)
        CANVA_DESIGN_CACHE_LOCKS.pop(user_id, None)


def _get_canva_design_cache_entry(user_id: str) -> Dict[str, Any]:
    entry = CANVA_DESIGN_CACHE.get(user_id)
    if entry is None:
        entry = {
            "items": [],
            "ids": set(),
            "continuation": None,
            "complete": False,
            "updated_at": time.time(),
        }
        CANVA_DESIGN_CACHE[user_id] = entry
    return entry


def _get_canva_design_cache_lock(user_id: str) -> asyncio.Lock:
    lock = CANVA_DESIGN_CACHE_LOCKS.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        CANVA_DESIGN_CACHE_LOCKS[user_id] = lock
    return lock


def _cache_canva_design_items(entry: Dict[str, Any], items: List[Dict[str, Any]]) -> int:
    ids = entry.setdefault("ids", set())
    if not isinstance(ids, set):
        ids = set(ids)
        entry["ids"] = ids

    cached_items = entry.setdefault("items", [])
    added = 0
    for d in items or []:
        design_id = d.get("id")
        if design_id:
            if design_id in ids:
                continue
            ids.add(design_id)
        cached_items.append(d)
        added += 1
    entry["updated_at"] = time.time()
    return added


async def _find_canva_design_with_cache(
    access_token: str,
    user_id: str,
    sku: str
) -> Optional[Dict[str, Any]]:
    _cleanup_canva_design_cache()
    entry = _get_canva_design_cache_entry(user_id)

    # Fast path: tenta encontrar no que já está em memória.
    found = canva_service.check_design_exists(entry.get("items", []), sku)
    if found is not None:
        entry["updated_at"] = time.time()
        return found
    if bool(entry.get("complete")):
        entry["updated_at"] = time.time()
        return None

    lock = _get_canva_design_cache_lock(user_id)
    async with lock:
        # Revalida após adquirir lock (outro request pode ter preenchido enquanto aguardava).
        entry = _get_canva_design_cache_entry(user_id)
        found = canva_service.check_design_exists(entry.get("items", []), sku)
        if found is not None:
            entry["updated_at"] = time.time()
            return found
        if bool(entry.get("complete")):
            entry["updated_at"] = time.time()
            return None

        seen_continuations = set()
        while True:
            continuation = entry.get("continuation")
            if continuation and continuation in seen_continuations:
                entry["complete"] = True
                entry["continuation"] = None
                entry["updated_at"] = time.time()
                return canva_service.check_design_exists(entry.get("items", []), sku)
            if continuation:
                seen_continuations.add(continuation)

            items, next_continuation = await canva_service.get_designs_page(
                access_token,
                continuation=continuation
            )
            _cache_canva_design_items(entry, items)
            entry["continuation"] = next_continuation

            # Se encontrou, devolve imediatamente.
            found = canva_service.check_design_exists(entry.get("items", []), sku)
            if found is not None:
                return found

            # Em caso de miss, continua até o fim para popular cache completo.
            if not next_continuation:
                entry["complete"] = True
                entry["continuation"] = None
                entry["updated_at"] = time.time()
                return None


def _set_canva_export_task(task_id: str, **updates):
    task = CANVA_EXPORT_TASKS.get(task_id, {})
    task.update(updates)
    task["updated_at"] = time.time()
    CANVA_EXPORT_TASKS[task_id] = task


def _canva_reauth_detail(message: str = "Sessao do Canva expirada. Reautorize o Canva.") -> Dict[str, str]:
    return {"code": "canva_reauth_required", "message": message}


def _apply_canva_token_data(canva_cfg: Dict[str, Any], token_data: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(canva_cfg or {})
    now_ts = int(time.time())
    expires_in_raw = token_data.get("expires_in")
    try:
        expires_in = int(expires_in_raw) if expires_in_raw is not None else None
    except (TypeError, ValueError):
        expires_in = None

    access_token = token_data.get("access_token")
    if access_token:
        updated["access_token"] = access_token
    refresh_token = token_data.get("refresh_token")
    if refresh_token:
        updated["refresh_token"] = refresh_token
    if token_data.get("scope") is not None:
        updated["scope"] = token_data.get("scope")
    updated["expires_in"] = expires_in_raw
    updated["token_obtained_at"] = now_ts
    if expires_in:
        updated["expires_at"] = now_ts + expires_in
    else:
        updated.pop("expires_at", None)
    return updated


async def _get_valid_canva_access_token(
    db: Session,
    user_id: str,
    force_refresh: bool = False
) -> tuple[str, UserConfig, Dict[str, Any]]:
    cfg = db.query(UserConfig).filter(UserConfig.user_id == user_id).first()
    if not cfg:
        raise HTTPException(status_code=401, detail=_canva_reauth_detail("Configuracao do Canva nao encontrada. Reautorize o Canva."))

    current_data = dict(cfg.data or {})
    canva_cfg = dict(current_data.get("canva", {}))
    client_id = canva_cfg.get("client_id")
    client_secret = canva_cfg.get("client_secret")
    access_token = canva_cfg.get("access_token")
    refresh_token = canva_cfg.get("refresh_token")

    expires_at_raw = canva_cfg.get("expires_at")
    try:
        expires_at = float(expires_at_raw) if expires_at_raw is not None else None
    except (TypeError, ValueError):
        expires_at = None

    now_ts = time.time()
    should_refresh = force_refresh or not access_token
    if expires_at is not None and now_ts >= (expires_at - 120):
        should_refresh = True
    # Migração de tokens antigos sem expires_at salvo.
    if expires_at is None and access_token and refresh_token:
        should_refresh = True

    if should_refresh:
        if not client_id or not client_secret or not refresh_token:
            raise HTTPException(status_code=401, detail=_canva_reauth_detail())
        try:
            refreshed = await canva_service.refresh_access_token(client_id, client_secret, refresh_token)
        except canva_service.CanvaAuthError:
            raise HTTPException(status_code=401, detail=_canva_reauth_detail())

        canva_cfg = _apply_canva_token_data(canva_cfg, refreshed)
        current_data["canva"] = canva_cfg
        cfg.data = current_data
        db.commit()
        access_token = canva_cfg.get("access_token")

    if not access_token:
        raise HTTPException(status_code=401, detail=_canva_reauth_detail())

    return access_token, cfg, canva_cfg


async def _get_canva_and_drive_context(
    db: Session,
    user_id: str,
    force_token_refresh: bool = False
) -> tuple[str, str, str]:
    access_token, cfg, _ = await _get_valid_canva_access_token(
        db=db,
        user_id=user_id,
        force_refresh=force_token_refresh
    )

    drive_cfg = cfg.data.get("google_drive", {}) if cfg else {}
    folder_id = drive_cfg.get("folder_id", "")
    credentials_json = drive_cfg.get("credentials_json", "")
    if not folder_id or not credentials_json:
        raise HTTPException(status_code=400, detail="Google Drive nao configurado no Admin.")

    return access_token, folder_id, credentials_json


async def _run_canva_export_flow(
    access_token: str,
    folder_id: str,
    credentials_json: str,
    sku: str,
    design_id: str,
    progress_hook=None
) -> Dict[str, Any]:
    print(f"DEBUG: Iniciando exportação do design {design_id} para SKU {sku}")

    job_id = await canva_service.start_export(access_token, design_id)
    print("DEBUG: Job ID exportação", job_id)

    export_urls = await canva_service.get_export_urls(access_token, job_id)
    print(f"DEBUG: URLs de download obtidas: {len(export_urls)}")
    if export_urls:
        print("DEBUG: Primeira URL:", export_urls[0][:30] + "...")

    extracted_files = await canva_service.download_and_validate_exports(export_urls, sku)
    total_files = len(extracted_files)
    print(f"DEBUG: Arquivos extraídos para upload: {total_files}")

    if total_files == 0:
        raise canva_service.CanvaServiceError("Nenhum arquivo PNG foi gerado na exportação do Canva.")

    service = await asyncio.to_thread(_build_drive_service, credentials_json)
    sku_folder_id = await asyncio.to_thread(_get_or_create_subfolder, service, folder_id, sku)

    if progress_hook:
        maybe = progress_hook(0, total_files, f"Baixando arquivos do Canva 0/{total_files}")
        if asyncio.iscoroutine(maybe):
            await maybe

    success_count = 0
    for filename, b_content in extracted_files:
        media = MediaIoBaseUpload(BytesIO(b_content), mimetype="image/png", resumable=False)
        existing_id = await asyncio.to_thread(_find_file_in_folder, service, sku_folder_id, filename)

        if existing_id:
            await asyncio.to_thread(
                lambda fid=existing_id, m=media: service.files().update(
                    fileId=fid,
                    media_body=m,
                    supportsAllDrives=True
                ).execute()
            )
        else:
            file_metadata = {
                "name": filename,
                "parents": [sku_folder_id]
            }
            await asyncio.to_thread(
                lambda md=file_metadata, m=media: service.files().create(
                    body=md,
                    media_body=m,
                    fields="id",
                    supportsAllDrives=True
                ).execute()
            )

        success_count += 1
        if progress_hook:
            maybe = progress_hook(success_count, total_files, f"Baixando arquivos do Canva {success_count}/{total_files}")
            if asyncio.iscoroutine(maybe):
                await maybe

    return {"count": success_count, "total": total_files}


async def _run_canva_export_task(
    task_id: str,
    user_id: str,
    access_token: str,
    folder_id: str,
    credentials_json: str,
    payload: CanvaExportIn
):
    try:
        _set_canva_export_task(
            task_id,
            user_id=user_id,
            status="running",
            phase="starting",
            saved=0,
            total=0,
            error=None,
            message="Iniciando exportação no Canva..."
        )

        async def _progress(saved: int, total: int, message: str):
            _set_canva_export_task(
                task_id,
                status="running",
                phase="uploading",
                saved=saved,
                total=total,
                message=message
            )

        result = await _run_canva_export_flow(
            access_token=access_token,
            folder_id=folder_id,
            credentials_json=credentials_json,
            sku=payload.sku,
            design_id=payload.design_id,
            progress_hook=_progress
        )

        _set_canva_export_task(
            task_id,
            status="success",
            phase="completed",
            saved=result["count"],
            total=result["total"],
            message=f"Sucesso! {result['count']} arquivos sincronizados.",
            result=result
        )
    except canva_service.CanvaAuthError:
        _set_canva_export_task(
            task_id,
            status="error",
            phase="failed",
            message="Sessao do Canva expirada. Clique em 'Reautorizar Canva'.",
            error="canva_reauth_required"
        )
    except Exception as e:
        _set_canva_export_task(
            task_id,
            status="error",
            phase="failed",
            message="Falha ao exportar arquivos do Canva.",
            error=str(e)
        )


@app.post("/api/canva/export-to-drive/start")
async def canva_export_to_drive_start(
    payload: CanvaExportIn,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db)
):
    if not GOOGLE_DRIVE_AVAILABLE:
        raise HTTPException(status_code=500, detail="Google Drive não disponível.")

    user_id = str(current_user.user_id)
    access_token, folder_id, credentials_json = await _get_canva_and_drive_context(db, user_id)

    _cleanup_canva_export_tasks()
    task_id = uuid4().hex
    _set_canva_export_task(
        task_id,
        id=task_id,
        user_id=user_id,
        status="queued",
        phase="queued",
        saved=0,
        total=0,
        error=None,
        message="Preparando exportação do Canva...",
        created_at=time.time()
    )

    asyncio.create_task(
        _run_canva_export_task(
            task_id=task_id,
            user_id=user_id,
            access_token=access_token,
            folder_id=folder_id,
            credentials_json=credentials_json,
            payload=payload
        )
    )

    return JSONResponse(content={"task_id": task_id, "status": "queued"})


@app.get("/api/canva/export-to-drive/status/{task_id}")
async def canva_export_to_drive_status(
    task_id: str,
    current_user: CurrentUser = Depends(get_current_user_master)
):
    _cleanup_canva_export_tasks()
    user_id = str(current_user.user_id)
    task = CANVA_EXPORT_TASKS.get(task_id)
    if not task or task.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Tarefa de exportação não encontrada.")

    saved = int(task.get("saved") or 0)
    total = int(task.get("total") or 0)
    progress = int((saved / total) * 100) if total > 0 else 0

    return JSONResponse(content={
        "task_id": task_id,
        "status": task.get("status"),
        "phase": task.get("phase"),
        "message": task.get("message"),
        "saved": saved,
        "total": total,
        "progress": progress,
        "error": task.get("error"),
        "result": task.get("result")
    })

@app.post("/api/canva/export-to-drive")
async def canva_export_to_drive(
    payload: CanvaExportIn,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db)
):
    if not GOOGLE_DRIVE_AVAILABLE:
        raise HTTPException(status_code=500, detail="Google Drive nao disponivel.")

    user_id = str(current_user.user_id)

    try:
        access_token, folder_id, credentials_json = await _get_canva_and_drive_context(db, user_id)
        result = await _run_canva_export_flow(
            access_token=access_token,
            folder_id=folder_id,
            credentials_json=credentials_json,
            sku=payload.sku,
            design_id=payload.design_id
        )
        return JSONResponse(content={
            "message": f"Sucesso! {result['count']} arquivos sincronizados.",
            "count": result["count"],
            "total": result["total"]
        })

    except canva_service.CanvaAuthError:
        # fallback: tenta um refresh forçado e repete uma única vez
        access_token, folder_id, credentials_json = await _get_canva_and_drive_context(
            db, user_id, force_token_refresh=True
        )
        result = await _run_canva_export_flow(
            access_token=access_token,
            folder_id=folder_id,
            credentials_json=credentials_json,
            sku=payload.sku,
            design_id=payload.design_id
        )
        return JSONResponse(content={
            "message": f"Sucesso! {result['count']} arquivos sincronizados.",
            "count": result["count"],
            "total": result["total"]
        })

    except HTTPException:
        raise
    except canva_service.CanvaValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except canva_service.CanvaServiceError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HttpError as e:
        raise HTTPException(status_code=502, detail=f"Erro do Google Drive API: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro durante exportacao: {str(e)}")


# ─── Mercado Livre OAuth ────────────────────────────────────────────────────


@app.get("/api/ml/auth")
async def ml_auth(
    request: Request,
    #current_user: CurrentUser = Depends(get_current_user_master),
):
    if not settings.ml_client_id or not settings.ml_client_secret:
        return JSONResponse(
            status_code=400,
            content={"error": "App ML não configurado. Defina ML_CLIENT_ID e ML_CLIENT_SECRET no ambiente."}
        )
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/ml/callback"
    auth_url = mercadolivre_service.get_auth_url(settings.ml_client_id, redirect_uri)
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    parsed = urlparse(auth_url)
    params = dict(parse_qs(parsed.query, keep_blank_values=True))
    params["state"] = [str(1)]
    new_query = urlencode({k: v[0] for k, v in params.items()})
    auth_url_with_state = urlunparse(parsed._replace(query=new_query))
    return RedirectResponse(url=auth_url_with_state)


@app.get("/api/ml/callback")
async def ml_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if error:
        return JSONResponse(
            status_code=400,
            content={"error": f"OAuth ML retornou erro: {error}"}
        )
    if not code or not state:
        return JSONResponse(status_code=400, content={"error": "Parâmetros OAuth ausentes."})

    user_id = state
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/ml/callback"

    try:
        token_data = await mercadolivre_service.exchange_code(
            client_id=settings.ml_client_id,
            client_secret=settings.ml_client_secret,
            code=code,
            redirect_uri=redirect_uri,
        )
    except mercadolivre_service.MLAuthError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    ml_user_id = str(token_data.get("user_id", ""))
    nickname = ""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.mercadolibre.com/users/{ml_user_id}",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                nickname = resp.json().get("nickname", "")
    except Exception:
        pass

    account = mercadolivre_service.apply_token_data(
        {"ml_user_id": ml_user_id, "nickname": nickname},
        token_data,
    )

    cfg = db.query(UserConfig).filter(UserConfig.user_id == user_id).first()
    if not cfg:
        cfg = UserConfig(user_id=user_id, data={})
        db.add(cfg)
    current_data = dict(cfg.data or {})
    ml_accounts: list = list(current_data.get("ml_accounts") or [])
    ml_accounts = [a for a in ml_accounts if str(a.get("ml_user_id")) != ml_user_id]
    ml_accounts.append(account)
    current_data["ml_accounts"] = ml_accounts
    cfg.data = current_data
    db.commit()

    return RedirectResponse(url="/?ml_auth=success")


@app.get("/api/ml/accounts")
async def ml_list_accounts(
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db),
):
    cfg = db.query(UserConfig).filter(UserConfig.user_id == str(current_user.user_id)).first()
    accounts = []
    if cfg:
        raw = list((cfg.data or {}).get("ml_accounts") or [])
        for a in raw:
            accounts.append({
                "ml_user_id": a.get("ml_user_id"),
                "nickname": a.get("nickname"),
                "expires_at": a.get("expires_at"),
            })
    return JSONResponse(content={"accounts": accounts})


@app.delete("/api/ml/accounts/{ml_user_id}")
async def ml_disconnect_account(
    ml_user_id: str,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db),
):
    cfg = db.query(UserConfig).filter(UserConfig.user_id == str(current_user.user_id)).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Configuração não encontrada.")
    current_data = dict(cfg.data or {})
    ml_accounts = [
        a for a in (current_data.get("ml_accounts") or [])
        if str(a.get("ml_user_id")) != ml_user_id
    ]
    current_data["ml_accounts"] = ml_accounts
    cfg.data = current_data
    db.commit()
    return JSONResponse(content={"ok": True})


# ─── Mercado Livre — Publicação ──────────────────────────────────────────────


class MLPublishRequest(BaseModel):
    sku: str
    marketplace: str = "mercadolivre"
    ml_user_id: str
    variant: str = "simple"


@app.post("/api/ml/publish")
async def ml_publish(
    payload: MLPublishRequest,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db),
):
    """
    Inicia publicação de anúncio no ML.
    Retorna job_id imediatamente; progresso via GET /api/ml/publish/{job_id}/events
    """
    _cleanup_ml_publish_jobs()
    user_id = str(current_user.user_id)

    sku_normalized = payload.sku.strip().upper()
    marketplace_normalized = "mercadolivre"
    workspace = db.query(SkuWorkspace).filter(
        SkuWorkspace.sku_normalized == sku_normalized,
        SkuWorkspace.marketplace_normalized == marketplace_normalized,
    ).first()

    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace não encontrado para este SKU.")

    ws_state = {
        "base_state": workspace.base_state or {},
        "versioned_state": workspace.versioned_state_current or {},
    }

    missing = mercadolivre_service.validate_workspace_for_publish(ws_state)
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"message": "Campos obrigatórios não preenchidos.", "missing_fields": missing},
        )

    cfg = db.query(UserConfig).filter(UserConfig.user_id == user_id).first()
    ml_accounts = list((cfg.data if cfg else {}).get("ml_accounts") or [])
    account = next((a for a in ml_accounts if str(a.get("ml_user_id")) == payload.ml_user_id), None)
    if not account:
        raise HTTPException(status_code=400, detail="Conta ML não encontrada. Conecte a conta em Configurações.")

    user_pricing_config = list((cfg.data if cfg else {}).get("pricing_config") or [])

    job_id = uuid4().hex
    ML_PUBLISH_JOBS[job_id] = {
        "user_id": user_id,
        "status": "queued",
        "events": [],
        "created_at": time.time(),
        "listing_id": None,
        "error": None,
    }

    asyncio.create_task(
        _run_ml_publish_job(
            job_id=job_id,
            user_id=user_id,
            workspace=ws_state,
            account=account,
            ml_accounts=ml_accounts,
            pricing_config=user_pricing_config,
            variant=payload.variant,
            db_user_id=user_id,
        )
    )

    return JSONResponse(content={"job_id": job_id})


def _emit_ml_event(job_id: str, step: str, message: str, **extra) -> None:
    """Registra um evento SSE no job em memória."""
    job = ML_PUBLISH_JOBS.get(job_id)
    if not job:
        return
    event = {"step": step, "message": message, **extra}
    job["events"].append(event)
    job["status"] = step


def _build_pricing_ctx_for_ml(pricing_config: list) -> Dict[str, Any]:
    """Extrai o contexto de precificação para o canal mercadolivre da config do usuário."""
    for entry in (pricing_config or []):
        if entry.get("channel") in ("mercadolivre", "meli", "ml"):
            return dict(entry)
    return {}


async def _run_ml_publish_job(
    job_id: str,
    user_id: str,
    workspace: Dict[str, Any],
    account: Dict[str, Any],
    ml_accounts: list,
    pricing_config: list,
    variant: str,
    db_user_id: str,
) -> None:
    """
    Executa o fluxo completo de publicação no ML.
    Emite eventos SSE via _emit_ml_event.
    Nunca lança exceção — captura tudo e emite evento de erro.
    """
    listing_id = None

    try:
        # ── 1. Renovar token ML se necessário ────────────────────────────
        _emit_ml_event(job_id, "token_refresh", "Verificando credenciais ML...")
        try:
            access_token, updated_account = await mercadolivre_service.get_valid_access_token(
                account=account,
                client_id=settings.ml_client_id,
                client_secret=settings.ml_client_secret,
            )
        except mercadolivre_service.MLAuthError as exc:
            _emit_ml_event(job_id, "error", str(exc) + " Reconecte a conta ML em Configurações.", failed_at="token_refresh")
            return

        if updated_account:
            def _persist_token():
                db_session = SessionLocal()
                try:
                    cfg_row = db_session.query(UserConfig).filter(UserConfig.user_id == db_user_id).first()
                    if cfg_row:
                        current_data = dict(cfg_row.data or {})
                        accounts = list(current_data.get("ml_accounts") or [])
                        updated_ml_user_id = updated_account.get("ml_user_id")
                        accounts = [a for a in accounts if str(a.get("ml_user_id")) != updated_ml_user_id]
                        accounts.append(updated_account)
                        current_data["ml_accounts"] = accounts
                        cfg_row.data = current_data
                        db_session.commit()
                finally:
                    db_session.close()
            await asyncio.to_thread(_persist_token)
            account = updated_account

        # ── 2. Montar payload do anúncio ──────────────────────────────────
        base = workspace.get("base_state") or {}
        fields = base.get("product_fields") or {}
        versioned = workspace.get("versioned_state") or {}
        variants_state = versioned.get("variants") or {}
        variant_state = variants_state.get(variant) or variants_state.get("simple") or {}
        prices = versioned.get("prices") or {}

        title_block = variant_state.get("title") or {}
        title_versions = title_block.get("versions") or []
        title_idx = title_block.get("current_index", -1)
        title_text = title_versions[title_idx] if 0 <= title_idx < len(title_versions) else ""

        desc_block = variant_state.get("description") or {}
        desc_versions = desc_block.get("versions") or []
        desc_idx = desc_block.get("current_index", -1)
        desc_text = desc_versions[desc_idx] if 0 <= desc_idx < len(desc_versions) else ""

        listing_price = float(prices.get("listing") or 0.0)
        weight_kg = float(fields.get("weight_kg") or 0)
        length_cm = float(fields.get("length_cm") or 0)
        width_cm = float(fields.get("width_cm") or 0)
        height_cm = float(fields.get("height_cm") or 0)
        category_id = str(fields.get("ml_category_id") or "")
        ml_attributes = fields.get("ml_attributes") or []
        listing_type_id = str(fields.get("ml_listing_type_id") or "gold_special")

        listing_payload = {
            "title": title_text,
            "category_id": category_id,
            "price": listing_price,
            "currency_id": "BRL",
            "available_quantity": 1,
            "condition": "new",
            "listing_type_id": listing_type_id,
            "status": "paused",
            "description": {"plain_text": desc_text},
            "shipping": {
                "mode": "me2",
                "local_pick_up": False,
                "free_shipping": False,
                "dimensions": {
                    "width": int(width_cm),
                    "height": int(height_cm),
                    "length": int(length_cm),
                    "weight": int(weight_kg * 1000),
                },
            },
        }
        if ml_attributes:
            listing_payload["attributes"] = ml_attributes

        # ── 3. Criar anúncio pausado ──────────────────────────────────────
        _emit_ml_event(job_id, "creating_listing", "Criando anúncio pausado no Mercado Livre...")
        try:
            listing_id = await mercadolivre_service.create_listing(access_token, listing_payload)
            ML_PUBLISH_JOBS[job_id]["listing_id"] = listing_id
        except mercadolivre_service.MLAPIError as exc:
            _emit_ml_event(job_id, "error", f"Falha ao criar anúncio: {exc}", failed_at="creating_listing")
            return

        # ── 4. Download imagens do Drive ──────────────────────────────────
        image_urls = list(fields.get("image_urls") or fields.get("drive_image_ids") or [])
        _emit_ml_event(job_id, "downloading_images", f"Baixando imagens do Google Drive... ({len(image_urls)} imagens)")

        image_bytes_list: list = []
        try:
            drive_cfg = {}
            def _load_drive_cfg():
                db_session = SessionLocal()
                try:
                    cfg_row = db_session.query(UserConfig).filter(UserConfig.user_id == db_user_id).first()
                    return dict((cfg_row.data or {}).get("google_drive") or {}) if cfg_row else {}
                finally:
                    db_session.close()
            drive_cfg = await asyncio.to_thread(_load_drive_cfg)

            credentials_json = drive_cfg.get("credentials_json", "")
            if not credentials_json:
                raise Exception("Google Drive não configurado. Configure as credenciais em Configurações.")

            service = await asyncio.to_thread(_build_drive_service, credentials_json)
            for img_ref in image_urls:
                file_id = img_ref if not img_ref.startswith("http") else img_ref.split("/d/")[-1].split("/")[0]
                content = await asyncio.to_thread(
                    lambda fid=file_id: service.files().get_media(fileId=fid, supportsAllDrives=True).execute()
                )
                filename = f"image_{len(image_bytes_list) + 1:03d}.jpg"
                image_bytes_list.append((filename, content))
        except Exception as exc:
            _emit_ml_event(
                job_id, "error",
                f"Falha ao baixar imagens do Drive: {exc}",
                failed_at="downloading_images",
                listing_id=listing_id,
            )
            return

        # ── 5. Upload imagens ao ML ───────────────────────────────────────
        _emit_ml_event(job_id, "uploading_images", "Enviando imagens ao Mercado Livre...")
        picture_ids: list = []
        for idx, (filename, img_bytes) in enumerate(image_bytes_list, start=1):
            try:
                pic_id = await mercadolivre_service.upload_image(access_token, img_bytes, filename)
                picture_ids.append(pic_id)
            except mercadolivre_service.MLAPIError as exc:
                _emit_ml_event(
                    job_id, "error",
                    f"Falha ao enviar imagem {idx}/{len(image_bytes_list)}: {exc}",
                    failed_at="uploading_images",
                    listing_id=listing_id,
                )
                return

        if picture_ids:
            try:
                await mercadolivre_service.attach_pictures_to_listing(access_token, listing_id, picture_ids)
            except mercadolivre_service.MLAPIError as exc:
                _emit_ml_event(
                    job_id, "error",
                    f"Falha ao associar imagens ao anúncio: {exc}",
                    failed_at="uploading_images",
                    listing_id=listing_id,
                )
                return

        # ── 6. Consultar frete ML ─────────────────────────────────────────
        _emit_ml_event(job_id, "checking_freight", "Consultando custo de frete no Mercado Livre...")
        try:
            ml_freight = await mercadolivre_service.get_listing_shipping_cost(access_token, listing_id)
        except mercadolivre_service.MLAPIError as exc:
            _emit_ml_event(
                job_id, "error",
                f"Falha ao consultar frete: {exc}",
                failed_at="checking_freight",
                listing_id=listing_id,
            )
            return

        shipping_cache = base.get("shipping_cost_cache") or {}
        adsgen_freight = float(shipping_cache.get("value") or 0.0)
        freight_result = mercadolivre_service.compare_freight(ml_freight, adsgen_freight)

        # ── 7. Comparar frete e ajustar se necessário ─────────────────────
        if freight_result["divergent"]:
            _emit_ml_event(
                job_id, "adjusting_price",
                f"Frete divergente (Ads Gen R$ {adsgen_freight:.2f} → ML R$ {ml_freight:.2f}) — recalculando preços...",
            )
            cost_price = float(fields.get("cost_price") or 0.0)
            pricing_ctx = _build_pricing_ctx_for_ml(pricing_config)
            new_price = mercadolivre_service.recalculate_price_with_new_freight(
                cost_price=cost_price,
                new_freight=ml_freight,
                pricing_ctx=pricing_ctx,
            )

            _emit_ml_event(job_id, "updating_listing", "Atualizando preço no anúncio...")
            try:
                await mercadolivre_service.update_listing_price(access_token, listing_id, new_price)
            except mercadolivre_service.MLAPIError as exc:
                _emit_ml_event(
                    job_id, "error",
                    f"Falha ao atualizar preço: {exc}",
                    failed_at="updating_listing",
                    listing_id=listing_id,
                )
                return

            if settings.whatsapp_service_url and settings.whatsapp_notify_phone:
                _emit_ml_event(job_id, "notifying_whatsapp", "Enviando notificação de divergência via WhatsApp...")
                try:
                    async with httpx.AsyncClient() as client:
                        await client.post(
                            settings.whatsapp_service_url,
                            json={
                                "phone": settings.whatsapp_notify_phone,
                                "message": (
                                    f"⚠️ Divergência de frete no anúncio {listing_id}:\n"
                                    f"Ads Gen: R$ {adsgen_freight:.2f} → ML: R$ {ml_freight:.2f}\n"
                                    f"Preço ajustado para R$ {new_price:.2f}"
                                ),
                            },
                            headers={"Authorization": f"Bearer {settings.whatsapp_service_token}"},
                            timeout=10.0,
                        )
                except Exception as exc:
                    logger.warning("Falha ao enviar notificação WhatsApp: %s", exc)

        # ── 8. Ativar anúncio ─────────────────────────────────────────────
        _emit_ml_event(job_id, "activating", "Ativando anúncio no Mercado Livre...")
        try:
            await mercadolivre_service.activate_listing(access_token, listing_id)
        except mercadolivre_service.MLAPIError as exc:
            _emit_ml_event(
                job_id, "error",
                f"Falha ao ativar anúncio: {exc}",
                failed_at="activating",
                listing_id=listing_id,
            )
            return

        listing_url = f"https://www.mercadolivre.com.br/anuncio/{listing_id}"
        _emit_ml_event(
            job_id, "done",
            "Anúncio publicado com sucesso!",
            listing_id=listing_id,
            listing_url=listing_url,
        )

    except Exception as exc:
        logger.exception("Erro inesperado no job ML %s: %s", job_id, exc)
        _emit_ml_event(
            job_id, "error",
            f"Erro inesperado: {exc}",
            failed_at="unknown",
            listing_id=listing_id,
        )


@app.get("/api/ml/publish/{job_id}/events")
async def ml_publish_events(
    job_id: str,
    current_user: CurrentUser = Depends(get_current_user_master),
):
    """Stream SSE de progresso da publicação ML."""
    _cleanup_ml_publish_jobs()
    user_id = str(current_user.user_id)

    job = ML_PUBLISH_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    if job.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Acesso negado.")

    async def event_generator():
        sent_index = 0
        max_wait_seconds = ML_PUBLISH_JOB_TTL
        waited = 0.0
        poll_interval = 0.3

        while waited < max_wait_seconds:
            job = ML_PUBLISH_JOBS.get(job_id)
            if not job:
                yield "data: {\"step\": \"error\", \"message\": \"Job expirado.\"}\n\n"
                return

            events = job.get("events") or []
            while sent_index < len(events):
                event_data = json.dumps(events[sent_index], ensure_ascii=False)
                yield f"data: {event_data}\n\n"
                step = events[sent_index].get("step")
                sent_index += 1
                if step in ("done", "error"):
                    return

            await asyncio.sleep(poll_interval)
            waited += poll_interval

        yield "data: {\"step\": \"error\", \"message\": \"Timeout: job excedeu 10 minutos.\"}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ─── Mercado Livre — Categorias ──────────────────────────────────────────────


class MLCategoryMapping(BaseModel):
    adsgen_name: str
    ml_category_id: str
    ml_category_name: str = ""


@app.get("/api/ml/categories")
async def ml_list_categories(
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db),
):
    cfg = db.query(UserConfig).filter(UserConfig.user_id == str(current_user.user_id)).first()
    mappings = list(((cfg.data if cfg else {}) or {}).get("ml_category_mappings") or [])
    return JSONResponse(content={"mappings": mappings})


@app.post("/api/ml/categories")
async def ml_add_category(
    payload: MLCategoryMapping,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db),
):
    user_id = str(current_user.user_id)
    cfg = db.query(UserConfig).filter(UserConfig.user_id == user_id).first()
    if not cfg:
        cfg = UserConfig(user_id=user_id, data={})
        db.add(cfg)
    current_data = dict(cfg.data or {})
    mappings = list(current_data.get("ml_category_mappings") or [])
    mappings = [m for m in mappings if m.get("adsgen_name") != payload.adsgen_name]
    mappings.append(payload.model_dump())
    current_data["ml_category_mappings"] = mappings
    cfg.data = current_data
    db.commit()
    return JSONResponse(content={"ok": True})


@app.get("/api/ml/categories/search")
async def ml_search_categories(
    q: str,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db),
):
    """Busca categorias ML por texto. Requer conta ML conectada."""
    user_id = str(current_user.user_id)
    cfg = db.query(UserConfig).filter(UserConfig.user_id == user_id).first()
    ml_accounts = list(((cfg.data if cfg else {}) or {}).get("ml_accounts") or [])
    if not ml_accounts:
        raise HTTPException(status_code=400, detail="Nenhuma conta ML conectada.")

    account = ml_accounts[0]
    try:
        access_token, updated = await mercadolivre_service.get_valid_access_token(
            account=account,
            client_id=settings.ml_client_id,
            client_secret=settings.ml_client_secret,
        )
    except mercadolivre_service.MLAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.mercadolibre.com/sites/MLB/domain_discovery/search",
            params={"q": q, "limit": 10},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Erro ao buscar categorias no ML.")
    return JSONResponse(content=resp.json())


@app.delete("/api/ml/categories/{adsgen_name}")
async def ml_remove_category(
    adsgen_name: str,
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db),
):
    user_id = str(current_user.user_id)
    cfg = db.query(UserConfig).filter(UserConfig.user_id == user_id).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Configuração não encontrada.")
    current_data = dict(cfg.data or {})
    mappings = [m for m in (current_data.get("ml_category_mappings") or []) if m.get("adsgen_name") != adsgen_name]
    current_data["ml_category_mappings"] = mappings
    cfg.data = current_data
    db.commit()
    return JSONResponse(content={"ok": True})


@app.post("/api/ml/categories/auto-populate")
async def ml_auto_populate_categories(
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db),
):
    """
    Escaneia os anúncios existentes da conta ML e pré-popula a tabela de categorias.
    """
    user_id = str(current_user.user_id)
    cfg = db.query(UserConfig).filter(UserConfig.user_id == user_id).first()
    ml_accounts = list(((cfg.data if cfg else {}) or {}).get("ml_accounts") or [])
    if not ml_accounts:
        raise HTTPException(status_code=400, detail="Nenhuma conta ML conectada.")

    account = ml_accounts[0]
    try:
        access_token, _ = await mercadolivre_service.get_valid_access_token(
            account=account,
            client_id=settings.ml_client_id,
            client_secret=settings.ml_client_secret,
        )
    except mercadolivre_service.MLAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    ml_user_id = account.get("ml_user_id")
    discovered: Dict[str, Dict[str, str]] = {}

    async with httpx.AsyncClient() as client:
        offset = 0
        limit = 50
        while True:
            resp = await client.get(
                f"https://api.mercadolibre.com/users/{ml_user_id}/items/search",
                params={"offset": offset, "limit": limit},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15.0,
            )
            if resp.status_code != 200:
                break
            data = resp.json()
            item_ids = data.get("results") or []
            if not item_ids:
                break

            for i in range(0, len(item_ids), 20):
                batch = item_ids[i:i+20]
                ids_param = ",".join(batch)
                detail_resp = await client.get(
                    "https://api.mercadolibre.com/items",
                    params={"ids": ids_param, "attributes": "id,category_id"},
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=15.0,
                )
                if detail_resp.status_code != 200:
                    continue
                for entry in detail_resp.json():
                    body = entry.get("body") or {}
                    cat_id = body.get("category_id")
                    if cat_id and cat_id not in discovered:
                        cat_resp = await client.get(
                            f"https://api.mercadolibre.com/categories/{cat_id}",
                            timeout=10.0,
                        )
                        cat_name = cat_id
                        if cat_resp.status_code == 200:
                            cat_name = cat_resp.json().get("name", cat_id)
                        discovered[cat_id] = {"ml_category_id": cat_id, "ml_category_name": cat_name}

            paging = data.get("paging") or {}
            total = paging.get("total", 0)
            offset += limit
            if offset >= total:
                break

    current_data = dict((cfg.data if cfg else {}) or {})
    existing = {m["ml_category_id"]: m for m in current_data.get("ml_category_mappings") or []}
    for cat_id, cat_info in discovered.items():
        if cat_id not in existing:
            existing[cat_id] = {
                "adsgen_name": cat_info["ml_category_name"],
                "ml_category_id": cat_id,
                "ml_category_name": cat_info["ml_category_name"],
            }

    current_data["ml_category_mappings"] = list(existing.values())
    cfg.data = current_data
    db.commit()

    return JSONResponse(content={
        "discovered": len(discovered),
        "total_mappings": len(existing),
    })


# ========= MAIN PARA RODAR DEBUGANDO =========


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=5002, reload=True)
