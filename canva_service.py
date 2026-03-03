import base64
import httpx
import urllib.parse
import zipfile
import io
import re
from typing import List, Dict, Any, Tuple, Optional
from fastapi import HTTPException
import asyncio
import hashlib
import secrets
import time

def generate_pkce() -> Tuple[str, str]:
    """Gera code_verifier e code_challenge para o fluxo PKCE."""
    # code_verifier: string aleatória de 43 a 128 caracteres
    code_verifier = secrets.token_urlsafe(96)[:128]
    
    # code_challenge = base64url(sha256(code_verifier))
    sha256_hash = hashlib.sha256(code_verifier.encode('ascii')).digest()
    code_challenge = base64.urlsafe_b64encode(sha256_hash).decode('ascii').rstrip('=')
    
    return code_verifier, code_challenge

CANVA_API_BASE = "https://api.canva.com/rest/v1"
CANVA_OAUTH_URL = "https://www.canva.com/api/oauth/authorize"

class CanvaServiceError(Exception):
    pass

class CanvaAuthError(Exception):
    pass

class CanvaValidationError(Exception):
    pass


def _retry_after_seconds(resp: httpx.Response) -> float:
    """Lê Retry-After quando disponível; fallback para 0."""
    raw = (resp.headers.get("Retry-After") or "").strip()
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def _extract_export_urls(job_data: dict) -> List[str]:
    """Extrai URLs de exportação do payload de job (formato atual e legado)."""
    job = job_data.get("job", {})
    job_urls = job.get("urls")
    if isinstance(job_urls, list):
        return [u for u in job_urls if isinstance(u, str) and u]
    if isinstance(job_urls, dict):
        legacy_exports = job_urls.get("exports", [])
        return [u for u in legacy_exports if isinstance(u, str) and u]
    return []

def get_auth_url(client_id: str, redirect_uri: str, code_challenge: str, state: str = "app") -> str:
    """Gera a URL de autorização do Canva com suporte a PKCE."""
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        # Scopes válidos no Canva Connect (docs atuais):
        # - List designs: design:meta:read
        # - Create/Get export job: design:content:read
        "scope": "design:content:read design:meta:read",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256"
    }
    return f"{CANVA_OAUTH_URL}?{urllib.parse.urlencode(params)}"

async def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str, code_verifier: str) -> dict:
    """Troca o código de autorização por um token de acesso usando PKCE."""
    auth_str = f"{client_id}:{client_secret}"
    auth_b64 = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
    
    headers = {
        "Authorization": f"Basic {auth_b64}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{CANVA_API_BASE}/oauth/token", headers=headers, data=data)
        if resp.status_code != 200:
            print("Erro Canva Token:", resp.text)
            raise CanvaAuthError(f"Erro ao obter token do Canva: {resp.text}")
            
        return resp.json()


async def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """Renova access token usando refresh_token."""
    auth_str = f"{client_id}:{client_secret}"
    auth_b64 = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")

    headers = {
        "Authorization": f"Basic {auth_b64}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{CANVA_API_BASE}/oauth/token", headers=headers, data=data)
        if resp.status_code != 200:
            raise CanvaAuthError(f"Erro ao renovar token do Canva: {resp.text}")
        return resp.json()

async def get_designs_page(access_token: str, continuation: Optional[str] = None) -> Tuple[List[dict], Optional[str]]:
    """Busca uma página de designs e retorna (items, next_continuation)."""
    headers = {
        "Authorization": f"Bearer {access_token}"
    }
    params = {"continuation": continuation} if continuation else None

    max_attempts = 6
    async with httpx.AsyncClient() as client:
        for attempt in range(max_attempts):
            resp = await client.get(
                f"{CANVA_API_BASE}/designs",
                headers=headers,
                params=params
            )

            if resp.status_code == 429:
                retry_after = _retry_after_seconds(resp)
                backoff = min(30.0, 2.0 ** attempt)
                await asyncio.sleep(retry_after if retry_after > 0 else backoff)
                continue

            if resp.status_code == 401:
                raise CanvaAuthError("Token do Canva expirado ou inválido.")
            if resp.status_code != 200:
                raise CanvaServiceError(f"Erro ao buscar designs: {resp.text}")

            data = resp.json()
            items = data.get("items", data.get("designs", [])) or []
            next_continuation = (
                data.get("continuation")
                or data.get("next_page_token")
                or data.get("nextContinuation")
            )
            return items, next_continuation

    raise CanvaServiceError("Canva rate limit ao buscar designs. Tente novamente em instantes.")


async def get_designs(access_token: str, max_pages: int = 1) -> List[dict]:
    """Busca designs do usuário com paginação via `continuation` quando disponível."""
    max_pages = max(1, int(max_pages or 1))
    all_items: List[dict] = []
    seen_ids = set()
    seen_continuations = set()
    continuation = None

    for _ in range(max_pages):
        if continuation and continuation in seen_continuations:
            break
        if continuation:
            seen_continuations.add(continuation)

        items, next_continuation = await get_designs_page(access_token, continuation=continuation)
        for d in items:
            design_id = d.get("id")
            if design_id:
                if design_id in seen_ids:
                    continue
                seen_ids.add(design_id)
            all_items.append(d)

        continuation = next_continuation
        if not continuation:
            break

    return all_items

def check_design_exists(designs: List[dict], sku: str) -> dict:
    """Verifica se existe um design cujo nome bata com o SKU.
       Pela regra, deve começar com o SKU. (Ex: SKU-ALL)
    """
    if not sku:
        return None

    sku_upper = sku.upper().strip()
    for d in designs:
        title = (d.get("title") or "").upper().strip()
        if title.startswith(sku_upper):
            return d
    return None

async def start_export(access_token: str, design_id: str) -> str:
    """Inicia a exportação de um design em formato PNG e retorna o job_id."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "design_id": design_id,
        "format": {
            "type": "png"
        }
    }
    # Respeita limite de criação de export jobs (20 req/min), com retry em 429.
    max_attempts = 6
    async with httpx.AsyncClient() as client:
        for attempt in range(max_attempts):
            resp = await client.post(f"{CANVA_API_BASE}/exports", headers=headers, json=payload)
            if resp.status_code == 200:
                job_id = resp.json().get("job", {}).get("id")
                if not job_id:
                    raise CanvaServiceError(f"Resposta de criação de export sem job_id: {resp.text}")
                return job_id

            if resp.status_code == 429:
                retry_after = _retry_after_seconds(resp)
                backoff = min(30.0, 2.0 ** attempt)
                await asyncio.sleep(retry_after if retry_after > 0 else backoff)
                continue

            if resp.status_code == 401:
                raise CanvaAuthError("Token do Canva expirado ou inválido.")

            raise CanvaServiceError(f"Erro ao iniciar exportação do design: {resp.text}")

    raise CanvaServiceError("Canva rate limit ao iniciar exportação. Tente novamente em instantes.")


async def get_export_urls(access_token: str, job_id: str) -> List[str]:
    """Poll status until success, then returns all download URLs in page order."""
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    # Respeita limite de consulta de job (120 req/min) com backoff exponencial.
    timeout_seconds = 180.0
    deadline = time.monotonic() + timeout_seconds
    delay = 1.0

    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            resp = await client.get(f"{CANVA_API_BASE}/exports/{job_id}", headers=headers)
            if resp.status_code == 429:
                retry_after = _retry_after_seconds(resp)
                await asyncio.sleep(retry_after if retry_after > 0 else delay)
                delay = min(delay * 1.5, 10.0)
                continue

            if resp.status_code == 401:
                raise CanvaAuthError("Token do Canva expirado ou inválido.")

            if resp.status_code != 200:
                raise CanvaServiceError(f"Erro ao checar status de exportação: {resp.text}")
                
            data = resp.json()
            status = data.get("job", {}).get("status")
            if status == "success":
                urls = _extract_export_urls(data)
                if urls:
                    return urls
                raise CanvaServiceError(f"Resposta de exportação sem URLs de download: {data}")
            elif status == "failed":
                raise CanvaServiceError(f"Exportação falhou no Canva: {data}")
                
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 10.0)

    raise CanvaServiceError("Timeout ao exportar design no Canva.")

async def download_and_validate_zip(url: str, sku: str) -> List[Tuple[str, bytes]]:
    """
    Baixa o resultado exportado.
    Como pedimos o formato PNG e pode ter várias páginas, o Canva retorna um ZIP (se +1 página) ou PNG direto.
    Retorna os arquivos extraídos preservando o nome original quando disponível.
    A regra final de normalização/preservação de nomes é aplicada em `download_and_validate_exports`.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, follow_redirects=True, timeout=60.0)
        resp.raise_for_status()
        
    content_type = (resp.headers.get("content-type", "") or "").lower()
    content_disposition_raw = resp.headers.get("content-disposition", "") or ""
    content_disposition = content_disposition_raw.lower()
    url_path = urllib.parse.urlparse(url).path.lower()
    payload = resp.content

    # Canva pode retornar content-type genérico em URL assinada.
    is_png = (
        "image/png" in content_type
        or ".png" in content_disposition
        or url_path.endswith(".png")
        or payload.startswith(b"\x89PNG\r\n\x1a\n")
    )
    is_zip = (
        "application/zip" in content_type
        or "application/x-zip-compressed" in content_type
        or ".zip" in content_disposition
        or url_path.endswith(".zip")
        or payload.startswith(b"PK\x03\x04")
        or payload.startswith(b"PK\x05\x06")
        or payload.startswith(b"PK\x07\x08")
    )

    extracted_files = []

    # Se for um único arquivo PNG (design de uma página)
    if is_png and not is_zip:
        # Tenta preservar nome original quando possível (header/URL).
        file_name = None
        m_utf8 = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition_raw, flags=re.IGNORECASE)
        if m_utf8:
            file_name = urllib.parse.unquote(m_utf8.group(1).strip().strip('"'))
        else:
            m_basic = re.search(r'filename="?([^";]+)"?', content_disposition_raw, flags=re.IGNORECASE)
            if m_basic:
                file_name = m_basic.group(1).strip()

        if not file_name:
            path_name = urllib.parse.urlparse(url).path.split("/")[-1]
            file_name = urllib.parse.unquote(path_name) if path_name else ""

        if not file_name or not file_name.lower().endswith(".png"):
            file_name = "page.png"

        extracted_files.append((file_name, payload))
        return extracted_files

    # Se for um arquivo ZIP
    if is_zip:
        with zipfile.ZipFile(io.BytesIO(payload)) as zip_ref:
            for file_info in zip_ref.infolist():
                if file_info.is_dir():
                    continue
                
                # Ignora arquivos ocultos do MacOS
                if file_info.filename.startswith("__MACOSX") or file_info.filename.startswith("."):
                    continue
                
                # Nome do arquivo de imagem dentro do ZIP (Canva usa título da página ou fallback numérico).
                base_name = file_info.filename.split("/")[-1]
                
                if not base_name.lower().endswith(".png"):
                    continue # só pegamos os pngs
                
                file_data = zip_ref.read(file_info.filename)
                extracted_files.append((base_name, file_data))

        if not extracted_files:
            raise CanvaServiceError("Exportação ZIP recebida, mas sem PNGs válidos para processar.")
        return extracted_files

    raise CanvaServiceError(
        "Formato de exportação inesperado recebido: "
        f"content_type={content_type or 'n/a'}, content_disposition={content_disposition or 'n/a'}, "
        f"url_path={url_path}"
    )


async def download_and_validate_exports(urls: List[str], sku: str) -> List[Tuple[str, bytes]]:
    """
    Baixa TODAS as URLs de export retornadas pelo job.
    Regra de nome:
    - Se começar com SKU, preserva exatamente o nome da página.
    - Caso contrário, normaliza para SKU###.png.
    A ordem final segue a ordem das URLs e dos itens internos de cada pacote.
    """
    if not urls:
        raise CanvaServiceError("Nenhuma URL de exportação foi retornada pelo Canva.")

    all_files: List[Tuple[str, bytes]] = []
    for url in urls:
        part_files = await download_and_validate_zip(url, sku)
        all_files.extend(part_files)

    sku_upper = sku.upper().strip()
    final_files: List[Tuple[str, bytes]] = []
    used_names_ci = set()
    auto_idx = 1

    def _next_auto_name() -> str:
        nonlocal auto_idx
        while True:
            candidate = f"{sku_upper}{auto_idx:03d}.png"
            auto_idx += 1
            if candidate.lower() not in used_names_ci:
                return candidate

    for original_name, file_data in all_files:
        safe_name = (original_name or "").strip()
        starts_with_sku = safe_name.upper().startswith(sku_upper)

        if starts_with_sku:
            final_name = safe_name
            # Garante extensão png para compatibilidade no upload/visualização.
            if not final_name.lower().endswith(".png"):
                final_name = f"{final_name}.png"

            # Evita colisão se páginas tiverem nomes iguais.
            if final_name.lower() in used_names_ci:
                final_name = _next_auto_name()
        else:
            final_name = _next_auto_name()

        used_names_ci.add(final_name.lower())
        final_files.append((final_name, file_data))

    return final_files


# Backward compatibility interna
async def get_export_url(access_token: str, job_id: str) -> str:
    urls = await get_export_urls(access_token, job_id)
    return urls[0]
