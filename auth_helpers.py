# auth_helpers.py

import secrets
import time
from typing import Dict, Any, Iterable, Tuple, Optional
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

import httpx
import jwt
from fastapi import HTTPException, status, Response
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import RedirectResponse

from config import settings


class CurrentUser(BaseModel):
    user_id: str
    email: str
    name: Optional[str] = None
    role: Optional[str] = None
    raw_claims: dict


# ==========================
# 1) Helpers de URL / redirect
# ==========================

def strip_forbidden_params_from_url(
        url: str,
        forbidden_params: Iterable[str] = ("token", "state"),
) -> str:
    """
    Remove parâmetros proibidos (ex.: token, state) de uma URL arbitrária,
    preservando path e demais parâmetros.

    Ex:
      http://x/auth?token=abc&foo=1  -> http://x/auth?foo=1
      http://x/auth?token=abc        -> http://x/auth
    """
    parsed = urlparse(url)
    query_items = parse_qsl(parsed.query, keep_blank_values=True)

    filtered_items = [
        (k, v) for (k, v) in query_items
        if k not in forbidden_params
    ]

    new_query = urlencode(filtered_items, doseq=True) if filtered_items else ""
    cleaned = parsed._replace(query=new_query)
    return urlunparse(cleaned)


def resolve_effective_redirect_from_request(request: Request) -> Optional[str]:
    """
    Calcula a URL que deve ser usada como redirect do app:

    - Se a requisição atual for /auth/gateway-login e houver parâmetro "redirect",
      usa o valor de 'redirect' (após limpar token/state).
    - Se a requisição atual for /auth/gateway-login mas *não* houver redirect,
      NÃO devemos gerar redirect → retorna None.
    - Para qualquer outra página:
      retorna a URL atual limpando token/state.

    Retorna:
        str: URL sanitizada de redirect
        None: caso não deva haver redirect
    """
    current_url_str = str(request.url)
    parsed = urlparse(current_url_str)
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    query_dict = dict(query_items)

    path = parsed.path.rstrip("/")

    # 🔹 Caso especial: já estamos no /auth/gateway-login
    if path == "/auth/gateway-login":
        # Se houver redirect, usamos ele
        if "redirect" in query_dict:
            inner_redirect = query_dict["redirect"]
            cleaned_inner = strip_forbidden_params_from_url(inner_redirect)
            return cleaned_inner

        # Se NÃO houver redirect → não devemos gerar redirect
        return None

    # 🔹 Cenário normal: página "real" do app → usamos a URL atual (limpa)
    cleaned_current = strip_forbidden_params_from_url(current_url_str)
    return cleaned_current


def _is_html_request(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept and not request.url.path.startswith("/api")


def _create_cookie_val(param, value):
    return f"{param}={value}; Path=/; HttpOnly; Max-Age=300; SameSite=Lax"


def _build_gateway_login_url(request: Request, state: Optional[str] = None) -> Tuple[str, str]:
    gateway_login_url = settings.gateway_login_url
    client_id = settings.app_slug
    state = state if state else secrets.token_urlsafe(16)
    base_url = request.base_url

    redirect_suffix = ""

    if _is_html_request(request):
        effective_redirect = resolve_effective_redirect_from_request(request)

        if effective_redirect:
            redirect_suffix = f"?redirect={effective_redirect}"

    login_url = (
        f"{gateway_login_url}"
        f"?client_id={client_id}"
        f"&redirect_uri={base_url}auth/gateway-login{redirect_suffix}"
        f"&state={state}"
    )

    return login_url, state


# ==========================
# 2) Validação local + refresh
# ==========================

def _decode_access_token_local(token: str) -> Dict[str, Any]:
    """
    Decodifica o JWT localmente usando o secret compartilhado.
    Lança jwt.ExpiredSignatureError se expirado.
    Lança jwt.InvalidTokenError se inválido.
    """
    return jwt.decode(
        token,
        settings.secret_key,  # mesmo segredo do Gateway
        algorithms=["HS256"],
        options={"verify_aud": False}
    )


async def _refresh_and_decode_via_gateway(request: Request) -> Optional[Dict[str, Any]]:
    """
    Tenta renovar o access token expirado chamando o /auth/refresh do Gateway.

    Premissas:
      - O refresh_token está em um cookie HttpOnly chamado "refresh_token",
        visível também para o domínio do Price Guard.
      - O Application Gateway expõe settings.gateway_refresh_url
        apontando para /auth/refresh.

    Retorna:
      - dict com claims do NOVO access token (formato similar ao introspect)
      - ou None em caso de falha (sem refresh_token, erro HTTP, etc.)
    """
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        return None

    gateway_refresh_url = getattr(settings, "gateway_refresh_url", None)
    if not gateway_refresh_url:
        return None

    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.post(
            gateway_refresh_url,
            cookies={"refresh_token": refresh_token},
        )

    if resp.status_code != 200:
        return None

    body = resp.json()
    new_token = body.get("access_token")
    if not new_token:
        return None

    # Decodifica o novo token localmente
    try:
        payload = _decode_access_token_local(new_token)
    except jwt.InvalidTokenError:
        return None

    app_slug = payload.get("app_slug")
    if app_slug != settings.app_slug:
        return None

    data: Dict[str, Any] = {
        "active": True,
        "user_id": payload.get("sub"),
        "app_slug": app_slug,
        "email": payload.get("email"),
        "name": payload.get("name"),
        "role": payload.get("role"),
        "exp": payload.get("exp"),
        "raw": payload,
        "_new_token": new_token,
    }

    return data


async def introspect_token(request: Request, token: str) -> Optional[Dict[str, Any]]:
    """
    NOVA lógica de introspecção:

    1) Tenta validar o access token LOCALMENTE usando o secret compartilhado.
       - Se token é válido e não expirou → retorna claims montadas localmente.
       - Se token é inválido (não é JWT, assinatura ruim, etc.) → retorna None.

    2) Se o token estiver EXPIRADO (jwt.ExpiredSignatureError),
       tenta chamar o /auth/refresh do Gateway para obter um novo access token.
       - Se o refresh funcionar → decodifica o novo token localmente e retorna claims.
       - Se falhar → retorna None.
    """
    payload: Optional[Dict[str, Any]] = None
    expired = False

    # 1) Tentativa de validação local
    try:
        payload = _decode_access_token_local(token)
    except jwt.ExpiredSignatureError:
        expired = True
    except jwt.InvalidTokenError:
        # token inválido por outro motivo -> não tenta refresh
        return None

    if payload is not None:
        # Token ainda válido localmente
        app_slug = payload.get("app_slug")
        if app_slug != settings.app_slug:
            return None

        return {
            "active": True,
            "user_id": payload.get("sub"),
            "app_slug": app_slug,
            "email": payload.get("email"),
            "name": payload.get("name"),
            "role": payload.get("role"),
            "exp": payload.get("exp"),
            "raw": payload,
        }

    # 2) Token expirado → tenta refresh no Gateway
    if expired:
        refreshed = await _refresh_and_decode_via_gateway(request)
        return refreshed

    # fallback genérico (não deveria chegar aqui)
    return None


# ==========================
# 3) Glue com FastAPI (redirect / user)
# ==========================

async def validate_token_or_redirect(request: Request, token: str) -> Dict[str, Any]:
    """
    Valida o token do Gateway com a nova lógica:

    - Primeiro tenta validação LOCAL do JWT.
    - Se expirado, tenta obter NOVO access token via /auth/refresh.
    - Garante que o token pertence ao app correto (app_id/app_slug).
    - Em caso de token inválido ou de app errado, dispara redirecionamento
      adequado para HTML ou API.

    Retorna:
        dict -> claims válidas do token.
    """
    data = await introspect_token(request, token)

    if not data:
        login_url, _ = _build_gateway_login_url(request)

        if _is_html_request(request):
            # Redireciona navegador
            raise HTTPException(
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers={"Location": login_url},
            )

        # Resposta apropriada para API
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"X-Redirect-Login": login_url},
        )

    # Verificar se o token pertence ao app correto
    app_slug = data.get("app_slug")
    if app_slug != settings.app_slug:
        login_url, _ = _build_gateway_login_url(request)

        if _is_html_request(request):
            raise HTTPException(
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers={"Location": login_url},
            )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong app_slug",
            headers={"X-Redirect-Login": login_url},
        )

    return data


# ==========================
# 4) Master: lógica comum + variações HTML/API
# ==========================

async def _get_current_user_common(
        request: Request,
        response: Response,
        *,
        html_mode: bool,
) -> CurrentUser:
    """
    Lógica central de autenticação:

    - Lê token do cookie pg_session ou Authorization: Bearer ...
    - Usa introspect_token (local + refresh se necessário)
    - Atualiza cookie pg_session se receber _new_token
    - Em caso de erro:
        * html_mode=True  -> redirects (307) para Gateway
        * html_mode=False -> 401 com X-Redirect-Login
    """
    token = request.cookies.get("local_app_session")

    # também aceita Authorization: Bearer ...
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()

    if not token:
        login_url, state = _build_gateway_login_url(request)

        if html_mode:
            # HTML → redirect + cookie pg_state
            raise HTTPException(
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers={
                    "Location": login_url,
                    "Set-Cookie": _create_cookie_val("local_app_state", state),
                },
            )

        # API → 401
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"X-Redirect-Login": login_url},
        )

    # Validação + possível refresh
    data = await introspect_token(request, token)

    if not data:
        login_url, _ = _build_gateway_login_url(request)

        if html_mode:
            raise HTTPException(
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers={"Location": login_url},
            )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"X-Redirect-Login": login_url},
        )

    app_slug = data.get("app_slug")
    if app_slug != settings.app_slug:
        login_url, _ = _build_gateway_login_url(request)

        if html_mode:
            raise HTTPException(
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers={"Location": login_url},
            )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong app_slug",
            headers={"X-Redirect-Login": login_url},
        )

    # Se o Gateway renovou o token, atualiza o cookie pg_session
    new_token = data.pop("_new_token", None)
    if new_token:
        # tenta calcular max_age a partir do exp, senão cai pra 3600
        max_age = 3600
        exp_value = data.get("exp")
        if isinstance(exp_value, (int, float)):
            remaining = int(exp_value - time.time())
            if remaining > 0:
                max_age = remaining

        response.set_cookie(
            key="local_app_session",
            value=new_token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=max_age,
            path="/",
        )

    return CurrentUser(
        user_id=data.get("user_id"),
        email=data.get("email"),
        name=data.get("name"),
        role=data.get("role"),
        raw_claims=data,
    )


# ========= 5) Master auto (decide por _is_html_request) =========

async def get_current_user_master(
        request: Request,
        response: Response,
) -> CurrentUser:
    """
    Função "master" que decide automaticamente qual fluxo usar:

    - Se _is_html_request(request) → fluxo HTML (redirects)
    - Caso contrário → fluxo API (401)
    """
    html_mode = _is_html_request(request)
    return await _get_current_user_common(request, response, html_mode=html_mode)


# ========= 6) Entry point function. Necessary for the gateway to call this app from the app grid. =========

async def gateway_login_helper(request: Request, token: str, state: str = None, redirect: str = "/"):
    """
    Endpoint que o gateway chama depois do clique no grid.
    Recebe o token, valida com o gateway e, se estiver ok, cria cookie de sessão.
    """
    user_data = await validate_token_or_redirect(request, token)

    if not user_data:
        raise HTTPException(400, "Invalid state")

    state_saved = request.cookies.get("local_app_state")

    if state_saved and state != state_saved:
        raise HTTPException(400, "Invalid state")

    response = RedirectResponse(url=redirect, status_code=status.HTTP_303_SEE_OTHER)

    response.set_cookie(
        key="local_app_session",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=3600,  # 1h, igual ao exp do token
    )

    response.delete_cookie("local_app_state")

    return response
