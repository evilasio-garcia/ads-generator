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
