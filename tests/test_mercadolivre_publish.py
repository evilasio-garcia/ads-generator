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
        mock_client.post = AsyncMock(return_value=_mock_http_post({"id": "MLB123456789", "permalink": "https://www.mercadolivre.com.br/produto/p/MLB12345"}))
        mock_cls.return_value = mock_client

        item_id, permalink = await mercadolivre_service.create_listing(
            access_token="TOKEN",
            payload=listing_payload,
        )

    assert item_id == "MLB123456789"
    assert permalink == "https://www.mercadolivre.com.br/produto/p/MLB12345"


@pytest.mark.asyncio
async def test_create_listing_raises_on_api_error():
    error_resp = MagicMock()
    error_resp.status_code = 400
    error_resp.json.return_value = {"message": "category required"}
    import httpx
    http_exc = httpx.HTTPStatusError("400", request=MagicMock(), response=error_resp)
    error_resp.raise_for_status.side_effect = http_exc

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
