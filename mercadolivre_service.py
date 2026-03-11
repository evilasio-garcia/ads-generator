# mercadolivre_service.py
"""Mercado Livre API integration — OAuth2, listings, images, shipping."""

import asyncio
import httpx
import logging
import urllib.parse
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

ML_AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
ML_API_BASE = "https://api.mercadolibre.com"
ML_SCOPES = "read write offline_access"


class MLAuthError(Exception):
    pass


class MLAPIError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class MLRateLimitError(MLAPIError):
    """Raised when ML API returns a retryable error after exhausting all retry attempts."""

    def __init__(self, method: str, url: str, attempts: int, status_code: int = 429):
        if status_code == 429:
            detail = "Rate limit (429)"
        else:
            detail = f"Erro transiente (HTTP {status_code})"
        super().__init__(
            f"{detail} persistente após {attempts} tentativas: {method.upper()} {url}",
            status_code=status_code,
        )


# ── Retry helper ──────────────────────────────────────────────────────

_RATE_LIMIT_MAX_RETRIES = 5
_RATE_LIMIT_BASE_DELAY = 2.0  # seconds
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    on_rate_limit: Optional[Callable[[int, float], None]] = None,
    **kwargs,
) -> httpx.Response:
    """Execute an HTTP request with automatic retry on transient errors.

    Retries on: 429 (rate limit), 5xx (server errors), and connection/timeout errors.
    Uses exponential backoff: 2s, 4s, 8s, 16s, 32s.
    Respects Retry-After header from ML API when present.
    Raises MLRateLimitError if all retries are exhausted.

    Args:
        client: httpx.AsyncClient instance.
        method: HTTP method (get, post, put, delete).
        url: Request URL.
        on_rate_limit: Optional callback(attempt, wait_seconds) for progress reporting.
        **kwargs: Forwarded to client.request().

    Returns:
        httpx.Response (caller is responsible for raise_for_status).

    Raises:
        MLRateLimitError: If retryable errors persist after all retries.
    """
    request_fn = getattr(client, method.lower())
    last_status = 0

    for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
        try:
            resp = await request_fn(url, **kwargs)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError,
                httpx.WriteError, httpx.PoolTimeout) as exc:
            if attempt >= _RATE_LIMIT_MAX_RETRIES:
                logger.warning(
                    "Connection error after %d retries: %s %s — %s",
                    _RATE_LIMIT_MAX_RETRIES, method.upper(), url, exc,
                )
                raise MLRateLimitError(method, url, _RATE_LIMIT_MAX_RETRIES, status_code=0)
            wait = _RATE_LIMIT_BASE_DELAY * (2 ** attempt)
            logger.info(
                "Connection error on %s %s — retry %d/%d in %.1fs — %s",
                method.upper(), url, attempt + 1, _RATE_LIMIT_MAX_RETRIES, wait, exc,
            )
            if on_rate_limit:
                on_rate_limit(attempt + 1, wait)
            await asyncio.sleep(wait)
            continue

        if resp.status_code not in _RETRYABLE_STATUS_CODES:
            return resp

        last_status = resp.status_code

        if attempt >= _RATE_LIMIT_MAX_RETRIES:
            logger.warning(
                "Retryable error (HTTP %d) after %d retries: %s %s",
                resp.status_code, _RATE_LIMIT_MAX_RETRIES, method.upper(), url,
            )
            raise MLRateLimitError(method, url, _RATE_LIMIT_MAX_RETRIES, status_code=resp.status_code)

        retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
        if retry_after:
            try:
                wait = float(retry_after)
            except (TypeError, ValueError):
                wait = _RATE_LIMIT_BASE_DELAY * (2 ** attempt)
        else:
            wait = _RATE_LIMIT_BASE_DELAY * (2 ** attempt)

        logger.info(
            "Retryable error (HTTP %d) on %s %s — retry %d/%d in %.1fs",
            resp.status_code, method.upper(), url, attempt + 1, _RATE_LIMIT_MAX_RETRIES, wait,
        )

        if on_rate_limit:
            on_rate_limit(attempt + 1, wait)

        await asyncio.sleep(wait)

    raise MLRateLimitError(method, url, _RATE_LIMIT_MAX_RETRIES, status_code=last_status)


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


async def create_listing(access_token: str, payload: Dict[str, Any]) -> tuple:
    """Cria anúncio pausado no ML. Retorna (item_id, permalink)."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await _request_with_retry(
                client, "post", ML_ITEMS_URL,
                json=payload,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30.0,
            )
            resp.raise_for_status()
        except MLRateLimitError:
            raise
        except httpx.HTTPStatusError as exc:
            body = {}
            try:
                body = exc.response.json()
            except Exception:
                pass
            logger.error("[ML create_listing] status=%s body=%s", exc.response.status_code, exc.response.text)
            msg = body.get("message") or f"Erro {exc.response.status_code} ao criar anúncio"
            cause = body.get("cause") or body.get("causes") or ""
            if cause:
                msg = f"{msg} | cause: {cause}"
            raise MLAPIError(msg, status_code=exc.response.status_code) from exc
        except Exception as exc:
            raise MLAPIError(f"Erro de comunicação ao criar anúncio: {exc}") from exc
    data = resp.json()
    return data["id"], data.get("permalink", "")


async def update_description(access_token: str, item_id: str, plain_text: str) -> None:
    """Cria ou atualiza a descrição de um anúncio no ML."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await _request_with_retry(
                client, "post",
                f"{ML_API_BASE}/items/{item_id}/description",
                json={"plain_text": plain_text},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15.0,
            )
            # 400 may mean description already exists — try PUT
            if resp.status_code == 400:
                resp = await _request_with_retry(
                    client, "put",
                    f"{ML_API_BASE}/items/{item_id}/description",
                    json={"plain_text": plain_text},
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=15.0,
                )
            resp.raise_for_status()
        except MLRateLimitError:
            raise
        except httpx.HTTPStatusError as exc:
            body = {}
            try:
                body = exc.response.json()
            except Exception:
                pass
            raise MLAPIError(
                body.get("message") or f"Erro {exc.response.status_code} ao atualizar descrição",
                status_code=exc.response.status_code,
            ) from exc


async def upload_image(access_token: str, image_bytes: bytes, filename: str) -> str:
    """Faz upload de uma imagem ao ML. Retorna o picture_id."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await _request_with_retry(
                client, "post", ML_PICTURES_UPLOAD_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                files={"file": (filename, image_bytes, "image/jpeg")},
                timeout=60.0,
            )
            resp.raise_for_status()
        except MLRateLimitError:
            raise
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
            resp = await _request_with_retry(
                client, "put",
                f"{ML_ITEMS_URL}/{item_id}",
                json={"pictures": pictures},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30.0,
            )
            resp.raise_for_status()
        except MLRateLimitError:
            raise
        except httpx.HTTPStatusError as exc:
            raise MLAPIError(
                f"Erro {exc.response.status_code} ao associar imagens ao anúncio",
                status_code=exc.response.status_code,
            ) from exc


async def get_seller_shipping_cost(
    access_token: str,
    item_id: str,
    ml_user_id: str,
    on_rate_limit: Optional[Callable[[int, float], None]] = None,
) -> float:
    """
    Consulta o custo de frete (list_cost) do vendedor para o anúncio.

    1. GET /items/{item_id} → verifica mandatory_free_shipping em shipping.tags
    2. GET /users/{ml_user_id}/shipping_options/free?item_id=...&free_shipping=...
       → retorna coverage.all_country.list_cost

    Retorna 0.0 se não encontrado.
    Retries automaticamente em caso de 429 (rate limit).
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client:
        # Step 1: fetch item to check mandatory_free_shipping
        resp_item = await _request_with_retry(
            client, "get",
            f"{ML_ITEMS_URL}/{item_id}",
            headers=headers,
            timeout=15.0,
            on_rate_limit=on_rate_limit,
        )
        if resp_item.status_code != 200:
            raise MLAPIError(
                f"Erro {resp_item.status_code} ao consultar item para frete",
                status_code=resp_item.status_code,
            )

        item_data = resp_item.json()
        shipping_tags = (item_data.get("shipping") or {}).get("tags") or []
        free_shipping = "mandatory_free_shipping" in shipping_tags

        # Step 2: fetch list_cost from user-level shipping options
        resp_ship = await _request_with_retry(
            client, "get",
            f"{ML_API_BASE}/users/{ml_user_id}/shipping_options/free",
            params={
                "item_id": item_id,
                "free_shipping": str(free_shipping).lower(),
            },
            headers=headers,
            timeout=15.0,
            on_rate_limit=on_rate_limit,
        )
        if resp_ship.status_code != 200:
            raise MLAPIError(
                f"Erro {resp_ship.status_code} ao consultar frete do vendedor",
                status_code=resp_ship.status_code,
            )

    data = resp_ship.json()
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
            resp = await _request_with_retry(
                client, "put",
                f"{ML_ITEMS_URL}/{item_id}",
                json={"price": round(new_price, 2)},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30.0,
            )
            resp.raise_for_status()
        except MLRateLimitError:
            raise
        except httpx.HTTPStatusError as exc:
            raise MLAPIError(
                f"Erro {exc.response.status_code} ao atualizar preço",
                status_code=exc.response.status_code,
            ) from exc


async def update_listing_attributes(access_token: str, item_id: str, attributes: list) -> None:
    """Atualiza atributos de um anúncio existente via PUT."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await _request_with_retry(
                client, "put",
                f"{ML_ITEMS_URL}/{item_id}",
                json={"attributes": attributes},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30.0,
            )
            resp.raise_for_status()
        except MLRateLimitError:
            raise
        except httpx.HTTPStatusError as exc:
            raise MLAPIError(
                f"Erro {exc.response.status_code} ao atualizar atributos",
                status_code=exc.response.status_code,
            ) from exc


async def update_listing_sale_terms(access_token: str, item_id: str, sale_terms: list) -> None:
    """Atualiza sale_terms (garantia) de um anúncio existente via PUT."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await _request_with_retry(
                client, "put",
                f"{ML_ITEMS_URL}/{item_id}",
                json={"sale_terms": sale_terms},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30.0,
            )
            resp.raise_for_status()
        except MLRateLimitError:
            raise
        except httpx.HTTPStatusError as exc:
            raise MLAPIError(
                f"Erro {exc.response.status_code} ao atualizar garantia",
                status_code=exc.response.status_code,
            ) from exc


async def activate_listing(access_token: str, item_id: str) -> None:
    """Ativa um anúncio pausado."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await _request_with_retry(
                client, "put",
                f"{ML_ITEMS_URL}/{item_id}",
                json={"status": "active"},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30.0,
            )
            resp.raise_for_status()
        except MLRateLimitError:
            raise
        except httpx.HTTPStatusError as exc:
            raise MLAPIError(
                f"Erro {exc.response.status_code} ao ativar anúncio",
                status_code=exc.response.status_code,
            ) from exc


async def close_listing(access_token: str, item_id: str) -> None:
    """Fecha (exclui) um anúncio no ML. Best-effort — não propaga erros."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await _request_with_retry(
                client, "put",
                f"{ML_ITEMS_URL}/{item_id}",
                json={"status": "closed"},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30.0,
            )
            if resp.status_code >= 400:
                logger.warning(
                    "Failed to close listing %s: status=%s", item_id, resp.status_code
                )
        except Exception as exc:
            logger.warning("Failed to close listing %s: %s", item_id, exc)


async def get_category_settings(access_token: str, category_id: str) -> dict:
    """Retorna o dict settings da categoria ML (inclui catalog_required)."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.mercadolibre.com/categories/{category_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
    if resp.status_code == 200:
        return resp.json().get("settings") or {}
    return {}


async def get_category_attributes(access_token: str, category_id: str) -> list:
    """Busca atributos de uma categoria ML via GET /categories/{id}/attributes."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{ML_API_BASE}/categories/{category_id}/attributes",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15.0,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MLAPIError(
                f"Erro {exc.response.status_code} ao buscar atributos da categoria {category_id}",
                status_code=exc.response.status_code,
            ) from exc
    return resp.json()


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
    """Recalcula o preço de tabela (listing) usando o novo custo de frete do ML."""
    return _ml_price_calculator.get_listing_price(
        cost_price=float(cost_price),
        shipping_cost=float(new_freight),
        ctx=pricing_ctx or {},
    )


def recalculate_all_prices_with_new_freight(
    cost_price: float,
    new_freight: float,
    pricing_ctx: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Recalcula todos os preços (listing, promo, wholesale) com o novo frete.

    Returns dict with keys: listing_price, promo_price, wholesale_tiers.
    """
    ctx = dict(pricing_ctx or {})
    cp = float(cost_price)
    sc = float(new_freight)

    listing_price = _ml_price_calculator.get_listing_price(cp, sc, ctx)
    promo_price = _ml_price_calculator.get_promo_price(cp, sc, ctx)
    wholesale_tiers = _ml_price_calculator.get_wholesale_tiers(cp, sc, ctx)

    return {
        "listing_price": listing_price,
        "promo_price": promo_price,
        "wholesale_tiers": [
            {"min_quantity": int(t.min_quantity), "price": float(t.price)}
            for t in wholesale_tiers
            if t.min_quantity > 1 and t.price > 0
        ],
    }


async def set_wholesale_prices(access_token: str, item_id: str, tiers: list) -> dict:
    """Register quantity-based prices (wholesale) on ML.

    tiers: [{"min_quantity": 10, "price": 28.50}, ...]
    """
    prices_payload = [{"id": "1"}]  # keep standard price
    for tier in tiers:
        prices_payload.append({
            "amount": tier["price"],
            "currency_id": "BRL",
            "conditions": {
                "context_restrictions": ["channel_marketplace", "user_type_business"],
                "min_purchase_unit": tier["min_quantity"],
            },
        })
    async with httpx.AsyncClient() as client:
        try:
            resp = await _request_with_retry(
                client, "post",
                f"{ML_API_BASE}/items/{item_id}/prices/standard/quantity",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"prices": prices_payload},
                timeout=15.0,
            )
            resp.raise_for_status()
        except MLRateLimitError:
            raise
        except httpx.HTTPStatusError as exc:
            raise MLAPIError(
                f"Erro {exc.response.status_code} ao cadastrar preços por quantidade",
                status_code=exc.response.status_code,
            ) from exc
    return resp.json()


async def get_seller_own_promotions(access_token: str, user_id: str) -> list:
    """List seller's own promotions (SELLER_CAMPAIGN and PRICE_DISCOUNT) that are active or pending."""
    seen = {}
    async with httpx.AsyncClient() as client:
        for status in ("started", "pending"):
            try:
                resp = await _request_with_retry(
                    client, "get",
                    f"{ML_API_BASE}/seller-promotions/users/{user_id}",
                    params={"app_version": "v2", "status": status},
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "version": "v2",
                    },
                    timeout=15.0,
                )
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                data = resp.json()
                for promo in (data.get("results") or []):
                    if promo.get("type") in ("SELLER_CAMPAIGN", "PRICE_DISCOUNT"):
                        pid = promo.get("id")
                        if pid and pid not in seen:
                            seen[pid] = promo
            except (MLRateLimitError, httpx.HTTPStatusError):
                continue
    return list(seen.values())


async def check_item_promotion_candidacy(
    access_token: str, promo_id: str, item_id: str,
) -> Optional[dict]:
    """Check if an item is a candidate for a SELLER_CAMPAIGN promotion.

    Returns candidate dict with keys like 'min_discounted_price',
    'max_discounted_price', 'original_price', etc. or None if not a candidate.
    """
    async with httpx.AsyncClient() as client:
        try:
            resp = await _request_with_retry(
                client, "get",
                f"{ML_API_BASE}/seller-promotions/promotions/{promo_id}/items",
                params={
                    "promotion_type": "SELLER_CAMPAIGN",
                    "app_version": "v2",
                    "status": "candidate",
                    "item_id": item_id,
                },
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15.0,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            results = data.get("results") or []
            for r in results:
                if r.get("id") == item_id:
                    return r
        except (MLRateLimitError, httpx.HTTPStatusError):
            pass
    return None


async def add_item_to_promotion(
    access_token: str, item_id: str, user_id: str,
    promo_id: str, promo_type: str, deal_price: float,
) -> dict:
    """Add item to a seller promotion with deal price."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await _request_with_retry(
                client, "post",
                f"{ML_API_BASE}/seller-promotions/items/{item_id}",
                params={"app_version": "v2"},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "promotion_id": promo_id,
                    "promotion_type": promo_type,
                    "deal_price": round(deal_price, 2),
                },
                timeout=15.0,
            )
            resp.raise_for_status()
        except MLRateLimitError:
            raise
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                body = exc.response.json()
                detail = body.get("message") or body.get("error") or ""
            except Exception:
                pass
            raise MLAPIError(
                f"Erro {exc.response.status_code} ao adicionar item à promoção {promo_id}: {detail}".rstrip(": "),
                status_code=exc.response.status_code,
            ) from exc
    return resp.json()


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
