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
