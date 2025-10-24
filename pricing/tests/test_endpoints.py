"""
Testes de integração para endpoints de pricing.

Para executar:
    pytest pricing/tests/test_endpoints.py -v

Nota: Requer servidor em execução ou usar TestClient do FastAPI
"""
import pytest
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)


def test_pricing_quote_success():
    """Testa endpoint POST /pricing/quote com dados válidos"""
    response = client.post(
        "/pricing/quote",
        json={"cost_price": 100.0, "channel": "mercadolivre"}
    )
    
    assert response.status_code == 200
    data = response.json()
    
    assert "listing_price" in data
    assert "wholesale_tiers" in data
    assert "aggressive_price" in data
    assert "promo_price" in data
    assert "breakdown" in data
    assert data["channel"] == "mercadolivre"
    
    # Validar tipos
    assert isinstance(data["listing_price"], (int, float))
    assert isinstance(data["wholesale_tiers"], list)
    assert len(data["wholesale_tiers"]) > 0


def test_pricing_quote_invalid_channel():
    """Testa endpoint com canal inválido"""
    response = client.post(
        "/pricing/quote",
        json={"cost_price": 100.0, "channel": "canal_inexistente"}
    )
    
    assert response.status_code == 422
    data = response.json()
    assert "supported_channels" in data["detail"]


def test_pricing_quote_negative_cost():
    """Testa endpoint com custo negativo"""
    response = client.post(
        "/pricing/quote",
        json={"cost_price": -10.0, "channel": "shopee"}
    )
    
    assert response.status_code == 422


def test_pricing_policies():
    """Testa endpoint GET /pricing/policies"""
    response = client.get("/pricing/policies")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "supported_channels" in data
    assert "policies" in data
    assert isinstance(data["supported_channels"], list)
    assert len(data["supported_channels"]) == 7


def test_pricing_validate_success():
    """Testa endpoint POST /pricing/validate com dados válidos"""
    response = client.post(
        "/pricing/validate",
        json={"cost_price": 100.0, "channel": "amazon"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True


def test_pricing_validate_invalid():
    """Testa endpoint POST /pricing/validate com dados inválidos"""
    response = client.post(
        "/pricing/validate",
        json={"cost_price": -5.0, "channel": "canal_invalido"}
    )
    
    assert response.status_code == 422
    data = response.json()
    assert "errors" in data["detail"]
    assert len(data["detail"]["errors"]) >= 2  # cost_price E channel inválidos
