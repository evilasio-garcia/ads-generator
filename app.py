# -*- coding: utf-8 -*-
import base64
import json
import os
import random
import re
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

app = FastAPI(title="Ads Generator API", version="2.1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static
if not os.path.isdir("static"):
    os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static", html=True), name="static")


@app.get("/", include_in_schema=False)
async def root_index():
    # Serve the SPA from /static/index.html
    index_path = os.path.join("static", "index.html")
    return FileResponse(index_path)


class Options(BaseModel):
    llm: str = Field("openai", description="openai | gemini")
    openai_api_key: str = ""
    openai_base_url: str = ""
    gemini_api_key: str = ""
    gemini_base_url: str = ""
    rules: Dict[str, Any] = Field(default_factory=dict)
    prompt_template: Optional[str] = None


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
    "- Sem “frete grátis”, “brinde”, “promoção” ou equivalentes.\n"
    "- Não use emojis. Escreva em português do Brasil.\n"
    "- Adapte o tom ao marketplace especificado.\n"
    "- Responda em JSON com as chaves: title, description, faq (array de objetos {q,a}), cards (array de objetos {title,text}).\n"
)


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
    import requests
    base = opts.openai_base_url.strip() or "https://api.openai.com/v1"
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {opts.openai_api_key}", "Content-Type": "application/json"}
    
    # Construir conteúdo com arquivos se houver
    if files_data and len(files_data) > 0:
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
        
        payload = {"model": "gpt-4o", "messages": [{"role": "user", "content": content_parts}], "temperature": 0.7, "max_tokens": 4096}
    else:
        payload = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "temperature": 0.7}
    
    r = requests.post(url, headers=headers, json=payload, timeout=90)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]


def call_gemini(prompt: str, opts: Options, files_data: Optional[List[Dict[str, Any]]] = None) -> str:
    import requests
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
    r = requests.post(url, json=payload, timeout=90)
    r.raise_for_status()
    data = r.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return json.dumps(data)


def parse_json_loose(s: str) -> Dict[str, Any]:
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
    return render_prompt_template(tpl, product, marketplace, specs)


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
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB por arquivo
    MAX_FILES = 10  # Máximo de arquivos
    MAX_TOTAL_SIZE = 20 * 1024 * 1024  # 20MB total
    
    # Tipos de arquivo aceitos
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
    tpl = opts.prompt_template or DEFAULT_PROMPT_TEMPLATE
    specs = "{}"
    
    base_prompt = render_prompt_template(tpl, product, marketplace, specs)
    
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


@app.post("/api/generate")
async def generate(
    request: Request,
    json_data: Optional[str] = Form(None),
    files: List[UploadFile] = File(default=[])
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
async def regen(payload: RegenIn):
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=5000, reload=True)
