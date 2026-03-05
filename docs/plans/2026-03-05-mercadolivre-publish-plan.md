# Mercado Livre Publish Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Publicar anúncios no Mercado Livre via API com OAuth2 persistente, verificação de frete, recálculo de preços, notificação WhatsApp e feedback em tempo real via SSE.

**Architecture:** Novo módulo `mercadolivre_service.py` encapsula toda a lógica de OAuth e chamadas à API ML. Os endpoints em `app.py` disparam um job em background com `asyncio.create_task` e expõem um stream SSE via `StreamingResponse`. O armazenamento de credenciais segue exatamente o padrão do Canva: `user_config.data["ml_accounts"]` como lista de contas no banco PostgreSQL.

**Tech Stack:** Python 3.11+, FastAPI, httpx (já no projeto), SQLAlchemy + PostgreSQL, asyncio, pytest + unittest.mock

---

## Contexto do Projeto

- **Framework:** FastAPI em `app.py` (arquivo único, ~3400 linhas)
- **DB:** PostgreSQL via SQLAlchemy. Config de usuário em `user_config.data` (JSONB). Sem Alembic necessário para esta feature — os novos campos são adicionados ao JSON livre.
- **Padrão background task:** `asyncio.create_task(...)` + dict global em memória (ver `CANVA_EXPORT_TASKS` e `_run_canva_export_task` em `app.py:3401`)
- **Padrão OAuth:** `canva_service.py` + endpoints `/api/canva/auth` e `/api/canva/callback` em `app.py:2801-2841`. Seguir exatamente este padrão.
- **Drive:** `_build_drive_service`, `_find_file_in_folder` em `app.py`. Imagens do SKU ficam em `Drive/{folder_id}/{SKU}/RAW_IMG/` ou `Drive/{folder_id}/{SKU}/`.
- **Testes:** todos em `tests/`, importam direto de `app.py` ou dos módulos. Usar `unittest.mock.patch` para mockar `httpx.AsyncClient`.
- **Rodar testes:** `pytest tests/` da raiz do projeto.

---

## Task 1: Configuração global do App ML

**Objetivo:** Adicionar `ml_client_id` e `ml_client_secret` ao `config.py`.

**Files:**
- Modify: `config.py`
- Test: `tests/test_mercadolivre_oauth.py` (criar)

**Step 1: Escrever o teste que verifica que as variáveis existem no settings**

```python
# tests/test_mercadolivre_oauth.py
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import Settings


def test_ml_client_id_and_secret_are_configurable():
    s = Settings(ml_client_id="test_id", ml_client_secret="test_secret")
    assert s.ml_client_id == "test_id"
    assert s.ml_client_secret == "test_secret"


def test_ml_settings_default_to_empty_string():
    s = Settings()
    assert isinstance(s.ml_client_id, str)
    assert isinstance(s.ml_client_secret, str)
```

**Step 2: Rodar e confirmar que falha**

```bash
pytest tests/test_mercadolivre_oauth.py -v
```
Esperado: `FAILED` — `Settings` não tem `ml_client_id`.

**Step 3: Implementar**

Em `config.py`, adicionar após `jwt_expires_minutes`:

```python
ml_client_id: str = ""
ml_client_secret: str = ""
```

**Step 4: Rodar e confirmar que passa**

```bash
pytest tests/test_mercadolivre_oauth.py -v
```
Esperado: `2 passed`.

**Step 5: Commit**

```bash
git add config.py tests/test_mercadolivre_oauth.py
git commit -m "feat(ml): add ml_client_id and ml_client_secret to settings"
```

---

## Task 2: Módulo `mercadolivre_service.py` — OAuth helpers

**Objetivo:** Criar o módulo com funções de URL de autorização, troca de código por token e refresh.

**Files:**
- Create: `mercadolivre_service.py`
- Test: `tests/test_mercadolivre_oauth.py`

**Contexto da API ML:**
- Auth URL: `https://auth.mercadolivre.com.br/authorization?response_type=code&client_id=...&redirect_uri=...`
- Token URL: `POST https://api.mercadolivre.com/oauth/token`
- Scopes necessários: `read write offline_access`
- Access token expira em 21600 segundos (6 horas)
- Refresh token válido por 180 dias

**Step 1: Adicionar testes de OAuth ao arquivo existente**

```python
# Adicionar em tests/test_mercadolivre_oauth.py
import json
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
import mercadolivre_service


def test_get_auth_url_contains_required_params():
    url = mercadolivre_service.get_auth_url(
        client_id="MY_APP_ID",
        redirect_uri="https://myapp.com/api/ml/callback"
    )
    assert "MY_APP_ID" in url
    assert "myapp.com" in url
    assert "response_type=code" in url
    assert "offline_access" in url


@pytest.mark.asyncio
async def test_exchange_code_returns_token_data():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "ACCESS",
        "refresh_token": "REFRESH",
        "expires_in": 21600,
        "user_id": 123456789
    }
    mock_response.raise_for_status = MagicMock()

    with patch("mercadolivre_service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await mercadolivre_service.exchange_code(
            client_id="ID",
            client_secret="SECRET",
            code="AUTH_CODE",
            redirect_uri="https://myapp.com/api/ml/callback"
        )

    assert result["access_token"] == "ACCESS"
    assert result["refresh_token"] == "REFRESH"
    assert result["user_id"] == 123456789


@pytest.mark.asyncio
async def test_refresh_token_returns_new_access_token():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "NEW_ACCESS",
        "refresh_token": "NEW_REFRESH",
        "expires_in": 21600,
        "user_id": 123456789
    }
    mock_response.raise_for_status = MagicMock()

    with patch("mercadolivre_service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await mercadolivre_service.refresh_access_token(
            client_id="ID",
            client_secret="SECRET",
            refresh_token="OLD_REFRESH"
        )

    assert result["access_token"] == "NEW_ACCESS"


@pytest.mark.asyncio
async def test_refresh_token_raises_on_401():
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.raise_for_status.side_effect = Exception("401")

    with patch("mercadolivre_service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with pytest.raises(mercadolivre_service.MLAuthError):
            await mercadolivre_service.refresh_access_token("ID", "SECRET", "BAD_REFRESH")
```

**Step 2: Rodar e confirmar que falha**

```bash
pytest tests/test_mercadolivre_oauth.py -v
```
Esperado: `ERROR` — `mercadolivre_service` não existe.

**Step 3: Criar `mercadolivre_service.py`**

```python
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
```

**Step 4: Rodar e confirmar que passa**

```bash
pytest tests/test_mercadolivre_oauth.py -v
```
Esperado: todos os testes de OAuth passam.

**Step 5: Commit**

```bash
git add mercadolivre_service.py tests/test_mercadolivre_oauth.py
git commit -m "feat(ml): add mercadolivre_service OAuth2 helpers"
```

---

## Task 3: Helper de renovação automática de token

**Objetivo:** Função `get_valid_access_token` que verifica expiração e renova automaticamente antes de qualquer chamada à API.

**Files:**
- Modify: `mercadolivre_service.py`
- Test: `tests/test_mercadolivre_oauth.py`

**Step 1: Adicionar testes**

```python
# Adicionar em tests/test_mercadolivre_oauth.py
import time as time_module


def _make_account(expires_offset: int, access: str = "OLD", refresh: str = "REF") -> dict:
    return {
        "ml_user_id": "123",
        "nickname": "LOJA",
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": int(time_module.time()) + expires_offset,
        "token_obtained_at": int(time_module.time()) - 100,
    }


@pytest.mark.asyncio
async def test_get_valid_access_token_returns_existing_when_not_expiring():
    account = _make_account(expires_offset=3600)  # expira em 1h — ok
    token, updated = await mercadolivre_service.get_valid_access_token(
        account=account,
        client_id="ID",
        client_secret="SECRET",
    )
    assert token == "OLD"
    assert updated is None  # sem alteração necessária


@pytest.mark.asyncio
async def test_get_valid_access_token_refreshes_when_near_expiry():
    account = _make_account(expires_offset=200)  # expira em < 5min — deve renovar

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "REFRESHED",
        "refresh_token": "NEW_REF",
        "expires_in": 21600,
        "user_id": 123,
    }
    mock_response.raise_for_status = MagicMock()

    with patch("mercadolivre_service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        token, updated = await mercadolivre_service.get_valid_access_token(
            account=account,
            client_id="ID",
            client_secret="SECRET",
        )

    assert token == "REFRESHED"
    assert updated is not None
    assert updated["access_token"] == "REFRESHED"


@pytest.mark.asyncio
async def test_get_valid_access_token_raises_when_refresh_fails():
    account = _make_account(expires_offset=200)

    with patch("mercadolivre_service.refresh_access_token", side_effect=MLAuthError("fail")):
        with pytest.raises(mercadolivre_service.MLAuthError):
            await mercadolivre_service.get_valid_access_token(
                account=account,
                client_id="ID",
                client_secret="SECRET",
            )
```

**Step 2: Rodar e confirmar que falha**

```bash
pytest tests/test_mercadolivre_oauth.py::test_get_valid_access_token_returns_existing_when_not_expiring -v
```
Esperado: `FAILED` — função não existe ainda.

**Step 3: Adicionar `get_valid_access_token` em `mercadolivre_service.py`**

```python
# Adicionar no final de mercadolivre_service.py

TOKEN_REFRESH_BUFFER_SECONDS = 300  # renova se faltar < 5 minutos


async def get_valid_access_token(
    account: Dict[str, Any],
    client_id: str,
    client_secret: str,
) -> tuple[str, Optional[Dict[str, Any]]]:
    """
    Retorna (access_token, updated_account_or_None).
    Se o token estiver próximo de expirar, renova automaticamente.
    updated_account_or_None é não-nulo apenas se houve renovação — o chamador
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
```

**Step 4: Rodar e confirmar que passa**

```bash
pytest tests/test_mercadolivre_oauth.py -v
```
Esperado: todos passam.

**Step 5: Commit**

```bash
git add mercadolivre_service.py tests/test_mercadolivre_oauth.py
git commit -m "feat(ml): add automatic token refresh in get_valid_access_token"
```

---

## Task 4: Endpoints OAuth em `app.py`

**Objetivo:** `/api/ml/auth`, `/api/ml/callback`, `GET /api/ml/accounts`, `DELETE /api/ml/accounts/{ml_user_id}`.

**Files:**
- Modify: `app.py`
- Modify: `app.py` (imports)

**Contexto:** Seguir o padrão exato dos endpoints do Canva em `app.py:2801-2960`. O `client_id` e `client_secret` vêm de `settings.ml_client_id` / `settings.ml_client_secret` (globais). As contas ML ficam em `user_config.data["ml_accounts"]` como lista. O nickname do usuário ML é obtido via `GET /users/me` após o exchange.

**Step 1: Adicionar import de `mercadolivre_service` no topo de `app.py`**

Após a linha `import canva_service`, adicionar:

```python
import mercadolivre_service
```

**Step 2: Adicionar endpoints OAuth no final de `app.py` (antes do `if __name__ == "__main__"`)**

```python
# ─── Mercado Livre OAuth ────────────────────────────────────────────────────


@app.get("/api/ml/auth")
async def ml_auth(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user_master),
):
    if not settings.ml_client_id or not settings.ml_client_secret:
        return JSONResponse(
            status_code=400,
            content={"error": "App ML não configurado. Defina ML_CLIENT_ID e ML_CLIENT_SECRET no ambiente."}
        )
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/ml/callback"
    auth_url = mercadolivre_service.get_auth_url(settings.ml_client_id, redirect_uri)
    # Salva user_id no state para recuperar no callback
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    parsed = urlparse(auth_url)
    params = dict(parse_qs(parsed.query, keep_blank_values=True))
    params["state"] = [str(current_user.user_id)]
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

    # Buscar nickname do usuário ML
    ml_user_id = str(token_data.get("user_id", ""))
    nickname = ""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.mercadolivre.com/users/{ml_user_id}",
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

    # Salvar/atualizar no user_config
    cfg = db.query(UserConfig).filter(UserConfig.user_id == user_id).first()
    if not cfg:
        cfg = UserConfig(user_id=user_id, data={})
        db.add(cfg)
    current_data = dict(cfg.data or {})
    ml_accounts: list = list(current_data.get("ml_accounts") or [])
    # Substituir conta existente com mesmo ml_user_id ou adicionar
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
```

**Step 3: Rodar todos os testes existentes para garantir que nada quebrou**

```bash
pytest tests/ -v
```
Esperado: todos os testes existentes passam.

**Step 4: Commit**

```bash
git add app.py mercadolivre_service.py
git commit -m "feat(ml): add OAuth2 connect/callback/list/disconnect endpoints"
```

---

## Task 5: API ML — criação de anúncio, upload de imagem, consulta de frete, ativação

**Objetivo:** Adicionar em `mercadolivre_service.py` as funções que chamam a API ML para criar anúncio pausado, fazer upload de imagem, consultar custo de frete do anúncio e ativar o anúncio.

**Files:**
- Modify: `mercadolivre_service.py`
- Test: `tests/test_mercadolivre_publish.py` (criar)

**Referência da API ML:**
- Criar anúncio: `POST https://api.mercadolivre.com/items` com `status: "paused"`
- Upload imagem: `POST https://api.mercadolivre.com/pictures/items/upload` (multipart)
- Associar imagem ao item: `PUT https://api.mercadolivre.com/items/{item_id}` com `{"pictures": [{"id": "..."}]}`
- Consultar frete do anúncio: `GET https://api.mercadolivre.com/items/{item_id}/shipping_options/free`
  → retorna `coverage.all_country.list_cost` = custo para o vendedor
- Atualizar preço: `PUT https://api.mercadolivre.com/items/{item_id}` com `{"price": ...}`
- Ativar: `PUT https://api.mercadolivre.com/items/{item_id}` com `{"status": "active"}`

**Step 1: Criar `tests/test_mercadolivre_publish.py` com testes das funções do serviço**

```python
# tests/test_mercadolivre_publish.py
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import mercadolivre_service


def _mock_http_post(json_body: dict, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


def _mock_http_get(json_body: dict, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


def _mock_http_put(json_body: dict, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_create_listing_paused_returns_item_id():
    listing_payload = {
        "title": "Produto Teste",
        "category_id": "MLB1051",
        "price": 99.99,
        "currency_id": "BRL",
        "available_quantity": 1,
        "condition": "new",
        "listing_type_id": "gold_special",
        "status": "paused",
        "shipping": {
            "mode": "me2",
            "local_pick_up": False,
            "free_shipping": False,
            "dimensions": {"width": 10, "height": 5, "length": 15, "weight": 300},
        },
    }

    with patch("mercadolivre_service.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=_mock_http_post({"id": "MLB123456789"}))
        mock_cls.return_value = mock_client

        item_id = await mercadolivre_service.create_listing(
            access_token="TOKEN",
            payload=listing_payload,
        )

    assert item_id == "MLB123456789"


@pytest.mark.asyncio
async def test_create_listing_raises_on_api_error():
    error_resp = MagicMock()
    error_resp.status_code = 400
    error_resp.json.return_value = {"message": "category required"}
    error_resp.raise_for_status.side_effect = Exception("400")

    with patch("mercadolivre_service.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=error_resp)
        mock_cls.return_value = mock_client

        with pytest.raises(mercadolivre_service.MLAPIError):
            await mercadolivre_service.create_listing("TOKEN", {})


@pytest.mark.asyncio
async def test_upload_image_returns_picture_id():
    with patch("mercadolivre_service.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=_mock_http_post({"id": "PICID_123"}))
        mock_cls.return_value = mock_client

        pic_id = await mercadolivre_service.upload_image(
            access_token="TOKEN",
            image_bytes=b"FAKEPNG",
            filename="produto_01.png",
        )

    assert pic_id == "PICID_123"


@pytest.mark.asyncio
async def test_get_listing_shipping_cost_returns_float():
    shipping_resp = {
        "coverage": {
            "all_country": {
                "list_cost": 18.5
            }
        }
    }

    with patch("mercadolivre_service.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_mock_http_get(shipping_resp))
        mock_cls.return_value = mock_client

        cost = await mercadolivre_service.get_listing_shipping_cost(
            access_token="TOKEN",
            item_id="MLB123456789",
        )

    assert cost == 18.5


@pytest.mark.asyncio
async def test_get_listing_shipping_cost_returns_zero_on_missing_key():
    with patch("mercadolivre_service.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_mock_http_get({}))
        mock_cls.return_value = mock_client

        cost = await mercadolivre_service.get_listing_shipping_cost("TOKEN", "MLB1")

    assert cost == 0.0


@pytest.mark.asyncio
async def test_activate_listing_calls_put_with_active_status():
    with patch("mercadolivre_service.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.put = AsyncMock(return_value=_mock_http_put({"id": "MLB123", "status": "active"}))
        mock_cls.return_value = mock_client

        await mercadolivre_service.activate_listing(access_token="TOKEN", item_id="MLB123")

        call_kwargs = mock_client.put.call_args
        assert "active" in str(call_kwargs)


@pytest.mark.asyncio
async def test_update_listing_price_calls_put_with_new_price():
    with patch("mercadolivre_service.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.put = AsyncMock(return_value=_mock_http_put({"id": "MLB123", "price": 149.99}))
        mock_cls.return_value = mock_client

        await mercadolivre_service.update_listing_price(
            access_token="TOKEN", item_id="MLB123", new_price=149.99
        )

        call_kwargs = mock_client.put.call_args
        assert "149.99" in str(call_kwargs) or 149.99 in str(call_kwargs)
```

**Step 2: Rodar e confirmar que falha**

```bash
pytest tests/test_mercadolivre_publish.py -v
```
Esperado: `FAILED` — funções não existem em `mercadolivre_service`.

**Step 3: Adicionar as funções em `mercadolivre_service.py`**

```python
# Adicionar no final de mercadolivre_service.py

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
```

**Step 4: Rodar e confirmar que passa**

```bash
pytest tests/test_mercadolivre_publish.py -v
```
Esperado: todos os testes passam.

**Step 5: Commit**

```bash
git add mercadolivre_service.py tests/test_mercadolivre_publish.py
git commit -m "feat(ml): add listing create, image upload, freight query and activate functions"
```

---

## Task 6: Lógica de comparação de frete e recálculo de preço

**Objetivo:** Função pura que compara o frete ML com o frete do Ads Gen e, se necessário, recalcula o preço usando a calculadora existente.

**Files:**
- Modify: `mercadolivre_service.py`
- Test: `tests/test_mercadolivre_freight_comparison.py` (criar)

**Contexto:** A calculadora de preço ML está em `pricing/calculators/mercadolivre.py`. A função `get_promo_price(cost_price, shipping_cost, ctx)` recalcula o preço com um novo custo de frete. O `ctx` vem do workspace `pricing_config` do usuário.

**Step 1: Criar `tests/test_mercadolivre_freight_comparison.py`**

```python
# tests/test_mercadolivre_freight_comparison.py
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import mercadolivre_service


def test_freight_ok_when_ml_cost_equal_to_adsgen():
    result = mercadolivre_service.compare_freight(
        ml_freight=18.50,
        adsgen_freight=18.50,
    )
    assert result["divergent"] is False
    assert result["ml_freight"] == 18.50
    assert result["adsgen_freight"] == 18.50


def test_freight_ok_when_ml_cost_lower_than_adsgen():
    result = mercadolivre_service.compare_freight(
        ml_freight=15.00,
        adsgen_freight=18.50,
    )
    assert result["divergent"] is False


def test_freight_divergent_when_ml_cost_higher():
    result = mercadolivre_service.compare_freight(
        ml_freight=22.00,
        adsgen_freight=18.50,
    )
    assert result["divergent"] is True
    assert result["ml_freight"] == 22.00
    assert result["adsgen_freight"] == 18.50


def test_recalculate_price_uses_new_freight():
    from pricing.calculators.mercadolivre import MercadoLivrePriceCalculator
    calc = MercadoLivrePriceCalculator()
    ctx = {
        "commission_percent": 0.175,
        "impostos": 0.12,
        "tacos": 0.05,
        "margem_contribuicao": 0.10,
        "lucro": 0.05,
    }
    cost_price = 50.0
    old_freight = 10.0
    new_freight = 22.0

    old_price = calc.get_promo_price(cost_price, old_freight, ctx)
    new_price = mercadolivre_service.recalculate_price_with_new_freight(
        cost_price=cost_price,
        new_freight=new_freight,
        pricing_ctx=ctx,
    )

    assert new_price > old_price
    assert new_price > 0


def test_recalculate_price_returns_zero_when_no_ctx():
    result = mercadolivre_service.recalculate_price_with_new_freight(
        cost_price=50.0,
        new_freight=22.0,
        pricing_ctx=None,
    )
    # Sem ctx a calculadora usa defaults — deve retornar um valor positivo
    assert result > 0
```

**Step 2: Rodar e confirmar que falha**

```bash
pytest tests/test_mercadolivre_freight_comparison.py -v
```
Esperado: `FAILED`.

**Step 3: Adicionar funções em `mercadolivre_service.py`**

```python
# Adicionar no final de mercadolivre_service.py
from typing import Optional
from pricing.calculators.mercadolivre import MercadoLivrePriceCalculator

_ml_price_calculator = MercadoLivrePriceCalculator()


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
```

**Step 4: Rodar e confirmar que passa**

```bash
pytest tests/test_mercadolivre_freight_comparison.py -v
```
Esperado: todos passam.

**Step 5: Commit**

```bash
git add mercadolivre_service.py tests/test_mercadolivre_freight_comparison.py
git commit -m "feat(ml): add freight comparison and price recalculation helpers"
```

---

## Task 7: Validação de campos do workspace antes de publicar

**Objetivo:** Função que recebe o estado do workspace e retorna lista de campos obrigatórios faltantes do ponto de vista do Ads Gen.

**Files:**
- Modify: `mercadolivre_service.py`
- Test: `tests/test_mercadolivre_publish.py`

**Campos obrigatórios para publicação:**

| Campo | Onde fica no workspace |
|---|---|
| Título | `versioned_state.variants.simple.title` (current_index válido e texto não-vazio) |
| Descrição | `versioned_state.variants.simple.description` |
| Imagens | `base_state.product_fields.drive_image_ids` ou `base_state.product_fields.image_urls` (lista não-vazia) |
| Preço calculado | `prices.listing` > 0 |
| Custo do produto | `base_state.product_fields.cost_price` > 0 |
| Frete do Ads Gen | `base_state.shipping_cost_cache` com valor > 0 |
| Peso | `base_state.product_fields.weight_kg` > 0 |
| Comprimento | `base_state.product_fields.length_cm` > 0 |
| Largura | `base_state.product_fields.width_cm` > 0 |
| Altura | `base_state.product_fields.height_cm` > 0 |
| Categoria ML | `base_state.product_fields.ml_category_id` não-vazio |

**Step 1: Adicionar testes em `tests/test_mercadolivre_publish.py`**

```python
# Adicionar em tests/test_mercadolivre_publish.py


def _full_workspace():
    """Retorna um workspace válido e completo."""
    return {
        "base_state": {
            "product_fields": {
                "cost_price": 50.0,
                "weight_kg": 0.5,
                "length_cm": 15.0,
                "width_cm": 10.0,
                "height_cm": 5.0,
                "ml_category_id": "MLB1051",
                "image_urls": ["https://drive.google.com/img1.png"],
            },
            "shipping_cost_cache": {"value": 18.5},
        },
        "versioned_state": {
            "variants": {
                "simple": {
                    "title": {"versions": ["Produto Incrível"], "current_index": 0},
                    "description": {"versions": ["Descrição completa"], "current_index": 0},
                    "faq_lines": [],
                    "card_lines": [],
                }
            },
            "prices": {"listing": 149.99},
        },
    }


def test_validate_workspace_passes_when_all_fields_present():
    missing = mercadolivre_service.validate_workspace_for_publish(_full_workspace())
    assert missing == []


def test_validate_workspace_reports_missing_title():
    ws = _full_workspace()
    ws["versioned_state"]["variants"]["simple"]["title"] = {"versions": [], "current_index": -1}
    missing = mercadolivre_service.validate_workspace_for_publish(ws)
    assert any("título" in m.lower() for m in missing)


def test_validate_workspace_reports_missing_images():
    ws = _full_workspace()
    ws["base_state"]["product_fields"]["image_urls"] = []
    missing = mercadolivre_service.validate_workspace_for_publish(ws)
    assert any("imagem" in m.lower() for m in missing)


def test_validate_workspace_reports_missing_weight():
    ws = _full_workspace()
    ws["base_state"]["product_fields"]["weight_kg"] = 0
    missing = mercadolivre_service.validate_workspace_for_publish(ws)
    assert any("peso" in m.lower() for m in missing)


def test_validate_workspace_reports_missing_category():
    ws = _full_workspace()
    ws["base_state"]["product_fields"]["ml_category_id"] = ""
    missing = mercadolivre_service.validate_workspace_for_publish(ws)
    assert any("categoria" in m.lower() for m in missing)


def test_validate_workspace_reports_multiple_missing():
    ws = _full_workspace()
    ws["base_state"]["product_fields"]["weight_kg"] = 0
    ws["base_state"]["product_fields"]["ml_category_id"] = ""
    ws["base_state"]["product_fields"]["image_urls"] = []
    missing = mercadolivre_service.validate_workspace_for_publish(ws)
    assert len(missing) >= 3
```

**Step 2: Rodar e confirmar que falha**

```bash
pytest tests/test_mercadolivre_publish.py::test_validate_workspace_passes_when_all_fields_present -v
```
Esperado: `FAILED`.

**Step 3: Adicionar `validate_workspace_for_publish` em `mercadolivre_service.py`**

```python
# Adicionar no final de mercadolivre_service.py


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
        missing.append("Imagens do anúncio não encontradas (verifique o Google Drive / Canva)")

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
```

**Step 4: Rodar e confirmar que passa**

```bash
pytest tests/test_mercadolivre_publish.py -v
```
Esperado: todos passam.

**Step 5: Commit**

```bash
git add mercadolivre_service.py tests/test_mercadolivre_publish.py
git commit -m "feat(ml): add workspace field validation for publish"
```

---

## Task 8: Endpoint `POST /api/ml/publish` + job em background

**Objetivo:** Endpoint que valida, cria o job em memória, dispara `asyncio.create_task` e retorna o `job_id`.

**Files:**
- Modify: `app.py`

**Contexto:** Seguir o padrão de `CANVA_EXPORT_TASKS` e `asyncio.create_task(_run_canva_export_task(...))` já em `app.py:3370-3412`. O workspace com estado atual é carregado do banco no momento da publicação.

**Step 1: Adicionar dict global de jobs e modelo de request no `app.py`**

Após a declaração de `CANVA_EXPORT_TASKS` (buscar no arquivo), adicionar:

```python
# Jobs de publicação ML em memória
ML_PUBLISH_JOBS: Dict[str, Any] = {}
ML_PUBLISH_JOB_TTL = 600  # 10 minutos


def _cleanup_ml_publish_jobs() -> None:
    now = time.time()
    expired = [k for k, v in ML_PUBLISH_JOBS.items() if now - v.get("created_at", 0) > ML_PUBLISH_JOB_TTL]
    for k in expired:
        del ML_PUBLISH_JOBS[k]


class MLPublishRequest(BaseModel):
    sku: str
    marketplace: str = "mercadolivre"
    ml_user_id: str  # qual conta ML usar (ml_user_id da conta conectada)
    variant: str = "simple"  # variante do workspace a publicar
```

**Step 2: Adicionar o endpoint `POST /api/ml/publish` no `app.py`**

```python
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

    # Carregar workspace do banco
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

    # Validar campos obrigatórios
    missing = mercadolivre_service.validate_workspace_for_publish(ws_state)
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"message": "Campos obrigatórios não preenchidos.", "missing_fields": missing},
        )

    # Carregar conta ML
    cfg = db.query(UserConfig).filter(UserConfig.user_id == user_id).first()
    ml_accounts = list((cfg.data if cfg else {}).get("ml_accounts") or [])
    account = next((a for a in ml_accounts if str(a.get("ml_user_id")) == payload.ml_user_id), None)
    if not account:
        raise HTTPException(status_code=400, detail="Conta ML não encontrada. Conecte a conta em Configurações.")

    # Carregar pricing_config do usuário para recálculo de preço
    user_pricing_config = list((cfg.data if cfg else {}).get("pricing_config") or [])

    # Criar job
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
```

**Step 3: Rodar testes existentes para garantir que nada quebrou**

```bash
pytest tests/ -v --ignore=tests/gui
```
Esperado: todos passam.

**Step 4: Commit**

```bash
git add app.py
git commit -m "feat(ml): add POST /api/ml/publish endpoint with background job"
```

---

## Task 9: Task em background `_run_ml_publish_job`

**Objetivo:** Função assíncrona que executa o fluxo completo de publicação e emite eventos no dict de jobs.

**Files:**
- Modify: `app.py`

**Nota sobre WhatsApp:** usar `httpx.AsyncClient.post` para chamar o serviço interno. A URL e token do serviço WhatsApp devem vir de `settings` (adicionar `whatsapp_service_url: str = ""` e `whatsapp_service_token: str = ""` em `config.py`).

**Step 1: Adicionar `whatsapp_service_url` e `whatsapp_service_token` em `config.py`**

```python
whatsapp_service_url: str = ""
whatsapp_service_token: str = ""
whatsapp_notify_phone: str = ""  # número do responsável a notificar
```

**Step 2: Adicionar `_emit_ml_event` helper e `_run_ml_publish_job` em `app.py`**

```python
def _emit_ml_event(job_id: str, step: str, message: str, **extra) -> None:
    """Registra um evento SSE no job em memória."""
    job = ML_PUBLISH_JOBS.get(job_id)
    if not job:
        return
    event = {"step": step, "message": message, **extra}
    job["events"].append(event)
    job["status"] = step


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
            # Persistir novo token no banco
            async with SessionLocal() as db_session:
                cfg = db_session.query(UserConfig).filter(UserConfig.user_id == db_user_id).first()
                if cfg:
                    current_data = dict(cfg.data or {})
                    accounts = list(current_data.get("ml_accounts") or [])
                    updated_ml_user_id = updated_account.get("ml_user_id")
                    accounts = [a for a in accounts if str(a.get("ml_user_id")) != updated_ml_user_id]
                    accounts.append(updated_account)
                    current_data["ml_accounts"] = accounts
                    cfg.data = current_data
                    db_session.commit()
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
                    "weight": int(weight_kg * 1000),  # gramas
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

        image_bytes_list: list[tuple[str, bytes]] = []
        try:
            drive_cfg = {}
            async with SessionLocal() as db_session:
                cfg_row = db_session.query(UserConfig).filter(UserConfig.user_id == db_user_id).first()
                if cfg_row:
                    drive_cfg = dict((cfg_row.data or {}).get("google_drive") or {})

            credentials_json = drive_cfg.get("credentials_json", "")
            if not credentials_json:
                raise Exception("Google Drive não configurado. Configure as credenciais em Configurações.")

            service = await asyncio.to_thread(_build_drive_service, credentials_json)
            for img_ref in image_urls:
                # img_ref pode ser file_id ou URL do Drive
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
        _emit_ml_event(job_id, "uploading_images", f"Enviando imagens ao Mercado Livre...")
        picture_ids: list[str] = []
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

            # Notificar WhatsApp (não bloqueia)
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
                    # Não bloqueia o fluxo

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


def _build_pricing_ctx_for_ml(pricing_config: list) -> Dict[str, Any]:
    """Extrai o contexto de precificação para o canal mercadolivre da config do usuário."""
    for entry in (pricing_config or []):
        if entry.get("channel") in ("mercadolivre", "meli", "ml"):
            return dict(entry)
    return {}
```

**Step 3: Rodar testes**

```bash
pytest tests/ -v --ignore=tests/gui
```
Esperado: todos passam.

**Step 4: Commit**

```bash
git add app.py config.py
git commit -m "feat(ml): add background publish job with full SSE event flow"
```

---

## Task 10: Endpoint SSE `GET /api/ml/publish/{job_id}/events`

**Objetivo:** Endpoint que abre um stream SSE e emite os eventos do job à medida que chegam.

**Files:**
- Modify: `app.py`

**Step 1: Adicionar o endpoint SSE em `app.py`**

```python
from fastapi.responses import StreamingResponse as _StreamingResponse
import asyncio as _asyncio


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

    return _StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

**Step 2: Rodar testes**

```bash
pytest tests/ -v --ignore=tests/gui
```
Esperado: todos passam.

**Step 3: Commit**

```bash
git add app.py
git commit -m "feat(ml): add SSE stream endpoint GET /api/ml/publish/{job_id}/events"
```

---

## Task 11: Tabela de categorias DE/PARA — backend

**Objetivo:** Endpoints para listar, adicionar, remover mapeamentos de categorias e buscar categorias no ML.

**Files:**
- Modify: `app.py`
- Test: `tests/test_mercadolivre_category_mapping.py` (criar)

**Armazenamento:** `user_config.data["ml_category_mappings"]` como lista:
```json
[{"adsgen_name": "Suplementos", "ml_category_id": "MLB1534", "ml_category_name": "Suplementos"}]
```

**Step 1: Criar `tests/test_mercadolivre_category_mapping.py`**

```python
# tests/test_mercadolivre_category_mapping.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import mercadolivre_service
import pytest


def test_find_ml_category_id_returns_match():
    mappings = [
        {"adsgen_name": "Suplementos", "ml_category_id": "MLB1534", "ml_category_name": "Suplementos Esportivos"},
        {"adsgen_name": "Eletrônicos", "ml_category_id": "MLB1051", "ml_category_name": "Eletronicos"},
    ]
    result = mercadolivre_service.find_ml_category_id(mappings, "Suplementos")
    assert result == "MLB1534"


def test_find_ml_category_id_returns_none_when_not_found():
    mappings = [{"adsgen_name": "Suplementos", "ml_category_id": "MLB1534", "ml_category_name": "Suplementos"}]
    result = mercadolivre_service.find_ml_category_id(mappings, "Calçados")
    assert result is None


def test_find_ml_category_id_case_insensitive():
    mappings = [{"adsgen_name": "Suplementos", "ml_category_id": "MLB1534", "ml_category_name": "Suplementos"}]
    result = mercadolivre_service.find_ml_category_id(mappings, "suplementos")
    assert result == "MLB1534"
```

**Step 2: Rodar e confirmar que falha**

```bash
pytest tests/test_mercadolivre_category_mapping.py -v
```

**Step 3: Adicionar `find_ml_category_id` em `mercadolivre_service.py`**

```python
def find_ml_category_id(
    mappings: list[Dict[str, Any]],
    adsgen_name: str,
) -> Optional[str]:
    """Busca o ml_category_id pelo nome Ads Gen (case-insensitive)."""
    needle = str(adsgen_name or "").strip().lower()
    for m in (mappings or []):
        if str(m.get("adsgen_name") or "").strip().lower() == needle:
            return m.get("ml_category_id")
    return None
```

**Step 4: Adicionar endpoints de categoria em `app.py`**

```python
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
    # Remove duplicata pelo adsgen_name antes de adicionar
    mappings = [m for m in mappings if m.get("adsgen_name") != payload.adsgen_name]
    mappings.append(payload.model_dump())
    current_data["ml_category_mappings"] = mappings
    cfg.data = current_data
    db.commit()
    return JSONResponse(content={"ok": True})


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
            "https://api.mercadolivre.com/sites/MLB/domain_discovery/search",
            params={"q": q, "limit": 10},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Erro ao buscar categorias no ML.")
    return JSONResponse(content=resp.json())
```

**Step 5: Rodar todos os testes**

```bash
pytest tests/ -v --ignore=tests/gui
```
Esperado: todos passam.

**Step 6: Commit**

```bash
git add app.py mercadolivre_service.py tests/test_mercadolivre_category_mapping.py
git commit -m "feat(ml): add category DE/PARA mapping endpoints and ML category search"
```

---

## Task 12: Auto-populate de categorias a partir de anúncios existentes da conta

**Objetivo:** Endpoint que escaneia os anúncios ativos da conta ML e pré-popula a tabela de categorias com os mapeamentos encontrados.

**Files:**
- Modify: `app.py`

**Step 1: Adicionar endpoint em `app.py`**

```python
@app.post("/api/ml/categories/auto-populate")
async def ml_auto_populate_categories(
    current_user: CurrentUser = Depends(get_current_user_master),
    db: Session = Depends(get_db),
):
    """
    Escaneia os anúncios existentes da conta ML e pré-popula a tabela de categorias.
    Anúncios são buscados via GET /users/{user_id}/items/search.
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
                f"https://api.mercadolivre.com/users/{ml_user_id}/items/search",
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

            # Buscar detalhes em lote (max 20 por chamada)
            for i in range(0, len(item_ids), 20):
                batch = item_ids[i:i+20]
                ids_param = ",".join(batch)
                detail_resp = await client.get(
                    f"https://api.mercadolivre.com/items",
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
                        # Buscar nome da categoria
                        cat_resp = await client.get(
                            f"https://api.mercadolivre.com/categories/{cat_id}",
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

    # Mesclar com mapeamentos existentes (não sobrescrever adsgen_name já definidos)
    current_data = dict((cfg.data if cfg else {}) or {})
    existing = {m["ml_category_id"]: m for m in current_data.get("ml_category_mappings") or []}
    for cat_id, cat_info in discovered.items():
        if cat_id not in existing:
            existing[cat_id] = {
                "adsgen_name": cat_info["ml_category_name"],  # default: mesmo nome do ML
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
```

**Step 2: Rodar testes**

```bash
pytest tests/ -v --ignore=tests/gui
```
Esperado: todos passam.

**Step 3: Commit**

```bash
git add app.py
git commit -m "feat(ml): add auto-populate category mappings from existing ML listings"
```

---

## Task 13: Rodar suite completa de testes

**Objetivo:** Garantir que todos os testes passam antes de considerar a feature pronta.

**Step 1: Rodar suite completa**

```bash
pytest tests/ -v
```

**Step 2: Se algum teste falhar**

Investigar a falha, corrigir a causa raiz (não o teste), e rodar novamente até tudo passar.

**Step 3: Commit final se houver correções**

```bash
git add -A
git commit -m "fix(ml): address test failures after full suite run"
```

---

## Resumo das entregas

| Arquivo | O que muda |
|---|---|
| `config.py` | +`ml_client_id`, `ml_client_secret`, `whatsapp_service_url`, `whatsapp_service_token`, `whatsapp_notify_phone` |
| `mercadolivre_service.py` | Novo: OAuth, token refresh, create/update/activate listing, upload image, get shipping cost, compare freight, recalculate price, validate workspace, find category |
| `app.py` | +endpoints ML OAuth, publish, SSE events, category CRUD, category search, auto-populate |
| `tests/test_mercadolivre_oauth.py` | Novo: testes OAuth e token refresh |
| `tests/test_mercadolivre_publish.py` | Novo: testes de criação de anúncio, upload, frete, ativação, validação de workspace |
| `tests/test_mercadolivre_freight_comparison.py` | Novo: testes de comparação de frete e recálculo de preço |
| `tests/test_mercadolivre_category_mapping.py` | Novo: testes de mapeamento DE/PARA de categorias |

**Nota importante — `listing_type_id`:** implementado como `fields.get("ml_listing_type_id") or "gold_special"` com fallback hardcoded. Após a infra estar funcional, rodar script de análise nos anúncios existentes da conta para determinar o valor correto e atualizar o default.
