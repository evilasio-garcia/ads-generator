# mercadolivre_service.py
"""Mercado Livre API integration — OAuth2, listings, images, shipping."""

import httpx
import urllib.parse
import time
from typing import Any, Dict, Optional

ML_AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolivre.com/oauth/token"
ML_API_BASE = "https://api.mercadolivre.com"
ML_SCOPES = "read write offline_access"


class MLAuthError(Exception):
    pass


class MLAPIError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


def get_auth_url(client_id: str, redirect_uri: str) -> str:
    """Gera a URL de autorização OAuth2 do Mercado Livre."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": ML_SCOPES,
    }
    return f"{ML_AUTH_URL}?{urllib.parse.urlencode(params)}"


async def exchange_code(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> Dict[str, Any]:
    """Troca o authorization code por access_token + refresh_token."""
    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    }
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(ML_TOKEN_URL, data=payload, timeout=15.0)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MLAuthError(f"Falha ao trocar código ML: {exc.response.status_code}") from exc
        except Exception as exc:
            raise MLAuthError(f"Erro de comunicação com ML: {exc}") from exc
    return resp.json()


async def refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> Dict[str, Any]:
    """Renova o access token usando o refresh token."""
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(ML_TOKEN_URL, data=payload, timeout=15.0)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MLAuthError(f"Falha ao renovar token ML: {exc.response.status_code}") from exc
        except Exception as exc:
            raise MLAuthError(f"Erro de comunicação com ML: {exc}") from exc
    return resp.json()


def apply_token_data(
    account: Dict[str, Any],
    token_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Aplica dados de token recebidos da API ML a um dict de conta. Retorna novo dict."""
    updated = dict(account or {})
    now_ts = int(time.time())
    expires_in = token_data.get("expires_in")
    if token_data.get("access_token"):
        updated["access_token"] = token_data["access_token"]
    if token_data.get("refresh_token"):
        updated["refresh_token"] = token_data["refresh_token"]
    if token_data.get("user_id"):
        updated["ml_user_id"] = str(token_data["user_id"])
    updated["token_obtained_at"] = now_ts
    if expires_in:
        updated["expires_at"] = now_ts + int(expires_in)
    else:
        updated.pop("expires_at", None)
    return updated


TOKEN_REFRESH_BUFFER_SECONDS = 300  # renova se faltar < 5 minutos


async def get_valid_access_token(
    account: Dict[str, Any],
    client_id: str,
    client_secret: str,
) -> tuple:
    """
    Retorna (access_token, updated_account_or_None).
    Se o token estiver proximo de expirar, renova automaticamente.
    updated_account_or_None e nao-nulo apenas se houve renovacao -- o chamador
    deve persistir o updated_account no banco.
    """
    now_ts = time.time()
    expires_at = account.get("expires_at")
    access_token = account.get("access_token", "")
    refresh_token = account.get("refresh_token", "")

    needs_refresh = (
        not access_token
        or expires_at is None
        or now_ts >= (float(expires_at) - TOKEN_REFRESH_BUFFER_SECONDS)
    )

    if not needs_refresh:
        return access_token, None

    if not refresh_token:
        raise MLAuthError("Refresh token ausente. Reconecte a conta ML.")

    token_data = await refresh_access_token(client_id, client_secret, refresh_token)
    updated = apply_token_data(account, token_data)
    return updated["access_token"], updated


ML_ITEMS_URL = f"{ML_API_BASE}/items"
ML_PICTURES_UPLOAD_URL = f"{ML_API_BASE}/pictures/items/upload"


async def create_listing(access_token: str, payload: Dict[str, Any]) -> str:
    """Cria anúncio pausado no ML. Retorna o item_id (ex: 'MLB123456789')."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                ML_ITEMS_URL,
                json=payload,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = {}
            try:
                body = exc.response.json()
            except Exception:
                pass
            raise MLAPIError(
                body.get("message") or f"Erro {exc.response.status_code} ao criar anúncio",
                status_code=exc.response.status_code,
            ) from exc
        except Exception as exc:
            raise MLAPIError(f"Erro de comunicação ao criar anúncio: {exc}") from exc
    return resp.json()["id"]


async def upload_image(access_token: str, image_bytes: bytes, filename: str) -> str:
    """Faz upload de uma imagem ao ML. Retorna o picture_id."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                ML_PICTURES_UPLOAD_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                files={"file": (filename, image_bytes, "image/jpeg")},
                timeout=60.0,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MLAPIError(
                f"Erro {exc.response.status_code} ao enviar imagem {filename}",
                status_code=exc.response.status_code,
            ) from exc
        except Exception as exc:
            raise MLAPIError(f"Erro de comunicação ao enviar imagem: {exc}") from exc
    return resp.json()["id"]


async def attach_pictures_to_listing(
    access_token: str, item_id: str, picture_ids: list[str]
) -> None:
    """Associa picture_ids já enviados ao anúncio."""
    pictures = [{"id": pid} for pid in picture_ids]
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.put(
                f"{ML_ITEMS_URL}/{item_id}",
                json={"pictures": pictures},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MLAPIError(
                f"Erro {exc.response.status_code} ao associar imagens ao anúncio",
                status_code=exc.response.status_code,
            ) from exc


async def get_listing_shipping_cost(access_token: str, item_id: str) -> float:
    """
    Consulta o custo de frete do anúncio para o vendedor.
    Retorna 0.0 se não encontrado.
    """
    url = f"{ML_ITEMS_URL}/{item_id}/shipping_options/free"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15.0,
            )
            if resp.status_code == 404:
                return 0.0
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MLAPIError(
                f"Erro {exc.response.status_code} ao consultar frete",
                status_code=exc.response.status_code,
            ) from exc
    data = resp.json()
    try:
        return float(
            data.get("coverage", {}).get("all_country", {}).get("list_cost", 0.0) or 0.0
        )
    except (TypeError, ValueError):
        return 0.0


async def update_listing_price(access_token: str, item_id: str, new_price: float) -> None:
    """Atualiza o preço de um anúncio existente."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.put(
                f"{ML_ITEMS_URL}/{item_id}",
                json={"price": round(new_price, 2)},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MLAPIError(
                f"Erro {exc.response.status_code} ao atualizar preço",
                status_code=exc.response.status_code,
            ) from exc


async def activate_listing(access_token: str, item_id: str) -> None:
    """Ativa um anúncio pausado."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.put(
                f"{ML_ITEMS_URL}/{item_id}",
                json={"status": "active"},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MLAPIError(
                f"Erro {exc.response.status_code} ao ativar anúncio",
                status_code=exc.response.status_code,
            ) from exc


def validate_workspace_for_publish(workspace: Dict[str, Any]) -> list[str]:
    """
    Valida se todos os campos obrigatórios do Ads Gen estão preenchidos.
    Retorna lista de mensagens de erro (vazia = tudo ok).
    Perspectiva do Ads Gen: o anúncio precisa estar 100% completo antes de publicar.
    """
    missing = []
    base = workspace.get("base_state") or {}
    fields = base.get("product_fields") or {}
    shipping_cache = base.get("shipping_cost_cache") or {}
    versioned = workspace.get("versioned_state") or {}
    variants = versioned.get("variants") or {}
    simple = variants.get("simple") or {}
    prices = versioned.get("prices") or {}

    # Título
    title_block = simple.get("title") or {}
    title_versions = title_block.get("versions") or []
    title_idx = title_block.get("current_index", -1)
    title_text = title_versions[title_idx] if 0 <= title_idx < len(title_versions) else ""
    if not str(title_text).strip():
        missing.append("Título do anúncio não preenchido")

    # Descrição
    desc_block = simple.get("description") or {}
    desc_versions = desc_block.get("versions") or []
    desc_idx = desc_block.get("current_index", -1)
    desc_text = desc_versions[desc_idx] if 0 <= desc_idx < len(desc_versions) else ""
    if not str(desc_text).strip():
        missing.append("Descrição do anúncio não preenchida")

    # Imagens
    image_urls = fields.get("image_urls") or fields.get("drive_image_ids") or []
    if not isinstance(image_urls, list) or len(image_urls) == 0:
        missing.append("Nenhuma imagem do anúncio encontrada (verifique o Google Drive / Canva)")

    # Preço
    listing_price = prices.get("listing") or 0.0
    if float(listing_price) <= 0:
        missing.append("Preço de venda não calculado")

    # Custo
    cost_price = fields.get("cost_price") or 0.0
    if float(cost_price) <= 0:
        missing.append("Custo do produto não informado")

    # Frete Ads Gen
    shipping_value = shipping_cache.get("value") or 0.0
    if float(shipping_value) <= 0:
        missing.append("Custo de frete do Ads Gen não calculado")

    # Peso e dimensões
    if not float(fields.get("weight_kg") or 0):
        missing.append("Peso do produto (weight_kg) não informado")
    if not float(fields.get("length_cm") or 0):
        missing.append("Comprimento do produto (length_cm) não informado")
    if not float(fields.get("width_cm") or 0):
        missing.append("Largura do produto (width_cm) não informada")
    if not float(fields.get("height_cm") or 0):
        missing.append("Altura do produto (height_cm) não informada")

    # Categoria ML
    if not str(fields.get("ml_category_id") or "").strip():
        missing.append("Categoria Mercado Livre não mapeada (configure em Integrações > Mercado Livre)")

    return missing


from pricing.calculators.mercadolivre import MercadoLivrePriceCalculator as _MLPriceCalc

_ml_price_calculator = _MLPriceCalc()


def compare_freight(ml_freight: float, adsgen_freight: float) -> Dict[str, Any]:
    """
    Compara o custo de frete do ML com o do Ads Gen.
    Retorna dict com 'divergent' (bool), 'ml_freight' e 'adsgen_freight'.
    """
    divergent = float(ml_freight) > float(adsgen_freight)
    return {
        "divergent": divergent,
        "ml_freight": float(ml_freight),
        "adsgen_freight": float(adsgen_freight),
    }


def recalculate_price_with_new_freight(
    cost_price: float,
    new_freight: float,
    pricing_ctx: Optional[Dict[str, Any]],
) -> float:
    """Recalcula o preço de venda usando o novo custo de frete do ML."""
    return _ml_price_calculator.get_promo_price(
        cost_price=float(cost_price),
        shipping_cost=float(new_freight),
        ctx=pricing_ctx or {},
    )


def find_ml_category_id(
    mappings: list,
    adsgen_name: str,
) -> Optional[str]:
    """Busca o ml_category_id pelo nome Ads Gen (case-insensitive)."""
    needle = str(adsgen_name or "").strip().lower()
    for m in (mappings or []):
        if str(m.get("adsgen_name") or "").strip().lower() == needle:
            return m.get("ml_category_id")
    return None
