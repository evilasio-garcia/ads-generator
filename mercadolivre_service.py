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
