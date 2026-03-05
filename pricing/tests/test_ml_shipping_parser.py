import asyncio
from typing import Any

import pytest

from pricing import ml_shipping


ML_MATRIX_HTML = """
<html>
  <body>
    <table>
      <tr>
        <th>Peso*</th>
        <th>R$ 0 a R$ 18,99*</th>
        <th>R$ 19 a R$ 48,99</th>
        <th>R$ 49 a R$ 78,99</th>
        <th>R$ 79 a R$ 99,99</th>
        <th>R$ 100 a R$ 119,99</th>
        <th>R$ 120 a R$ 149,99</th>
        <th>R$ 150 a R$ 199,99</th>
        <th>A partir de R$ 200</th>
      </tr>
      <tr>
        <td>Até 0,3 kg</td>
        <td>R$ 5,65</td>
        <td>R$ 6,55</td>
        <td>R$ 7,75</td>
        <td>R$ 12,35</td>
        <td>R$ 14,35</td>
        <td>R$ 16,45</td>
        <td>R$ 18,45</td>
        <td>R$ 20,95</td>
      </tr>
      <tr>
        <td>De 0,3 a 0,5 kg</td>
        <td>R$ 5,95</td>
        <td>R$ 6,65</td>
        <td>R$ 7,85</td>
        <td>R$ 13,25</td>
        <td>R$ 15,45</td>
        <td>R$ 17,65</td>
        <td>R$ 19,85</td>
        <td>R$ 22,55</td>
      </tr>
      <tr>
        <td>De 0,5 a 1 kg</td>
        <td>R$ 6,05</td>
        <td>R$ 6,75</td>
        <td>R$ 7,95</td>
        <td>R$ 13,85</td>
        <td>R$ 16,15</td>
        <td>R$ 18,45</td>
        <td>R$ 20,75</td>
        <td>R$ 23,65</td>
      </tr>
      <tr>
        <td>Mais de 150 kg</td>
        <td>R$ 153,95</td>
        <td>R$ 162,95</td>
        <td>R$ 174,95</td>
        <td>R$ 174,95</td>
        <td>R$ 174,95</td>
        <td>R$ 174,95</td>
        <td>R$ 174,95</td>
        <td>R$ 174,95</td>
      </tr>
    </table>
    <table>
      <tr><th>Peso</th><th>R$ 0 a R$ 78,99*</th></tr>
      <tr><td>Até 0,3 kg</td><td>R$ 12,35</td></tr>
      <tr><td>De 0,3 a 0,5 kg</td><td>R$ 13,25</td></tr>
      <tr><td>Mais de 150 kg</td><td>R$ 174,95</td></tr>
    </table>
  </body>
</html>
"""


class _FakeResponse:
    status_code = 200

    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self) -> None:
        return None


def _find_range(tables: list[dict[str, Any]], min_price: float, max_price: float) -> dict[str, Any]:
    for table in tables:
        if table["min_price"] == pytest.approx(min_price) and table["max_price"] == pytest.approx(max_price):
            return table
    raise AssertionError(f"Range {min_price}-{max_price} not found")


def test_fetch_shipping_tables_parses_matrix_layout(monkeypatch):
    async def fake_get(self, url, headers=None, timeout=None):  # noqa: ANN001
        return _FakeResponse(ML_MATRIX_HTML)

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    tables = asyncio.run(ml_shipping._fetch_shipping_tables())

    # New ML layout is matrix: one table contains multiple sale-price ranges.
    range_79_99 = _find_range(tables, 79.0, 99.99)
    range_200_inf = _find_range(tables, 200.0, float("inf"))

    assert len(range_79_99["tiers"]) == 4
    assert range_79_99["tiers"][0]["max_weight"] == pytest.approx(0.3)
    assert range_79_99["tiers"][0]["price"] == pytest.approx(12.35)
    assert range_79_99["tiers"][1]["max_weight"] == pytest.approx(0.5)
    assert range_79_99["tiers"][1]["price"] == pytest.approx(13.25)
    assert range_79_99["tiers"][2]["max_weight"] == pytest.approx(1.0)
    assert range_79_99["tiers"][2]["price"] == pytest.approx(13.85)
    assert range_79_99["tiers"][3]["max_weight"] == float("inf")
    assert range_79_99["tiers"][3]["price"] == pytest.approx(174.95)

    assert range_200_inf["tiers"][2]["price"] == pytest.approx(23.65)
    assert all(tier["max_weight"] > 0 or tier["max_weight"] == float("inf") for tier in range_200_inf["tiers"])


def test_get_shipping_cost_uses_parsed_ranges(monkeypatch):
    # Reset cache to avoid bleed from other tests.
    ml_shipping._shipping_cache["data"] = None
    ml_shipping._shipping_cache["last_fetched"] = None

    async def fake_get(self, url, headers=None, timeout=None):  # noqa: ANN001
        return _FakeResponse(ML_MATRIX_HTML)

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    # Base <= 78.99 still follows business rule and returns zero.
    assert asyncio.run(ml_shipping.get_shipping_cost(cost_price=24.9, weight_kg=0.2)) == pytest.approx(0.0)

    # 49.8 * 2 = 99.6 -> range 79..99.99, weight 0.4 -> 13.25
    assert asyncio.run(ml_shipping.get_shipping_cost(cost_price=49.8, weight_kg=0.4)) == pytest.approx(13.25)

    # 99.6 * 2 = 199.2 -> range 150..199.99, weight 0.8 -> 20.75
    assert asyncio.run(ml_shipping.get_shipping_cost(cost_price=99.6, weight_kg=0.8)) == pytest.approx(20.75)

    # Explicit reference price can force higher range.
    assert asyncio.run(
        ml_shipping.get_shipping_cost(cost_price=10.0, weight_kg=0.3, reference_price=220.0)
    ) == pytest.approx(20.95)
