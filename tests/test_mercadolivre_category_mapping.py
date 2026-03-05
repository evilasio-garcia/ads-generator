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
