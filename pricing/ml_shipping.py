"""Mercado Livre shipping table scraping and lookup."""

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup

# Scraper settings
ML_SHIPPING_URL = "https://www.mercadolivre.com.br/ajuda/40538"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Cache
_shipping_cache: Dict[str, Any] = {"data": None, "last_fetched": None}
CACHE_TTL = timedelta(hours=12)

logger = logging.getLogger(__name__)
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


class MLShippingError(Exception):
    pass


def _is_valid_numeric(value: Any) -> bool:
    return isinstance(value, (int, float))


def _is_shipping_tables_layout_valid(tables: Any) -> bool:
    if not isinstance(tables, list) or not tables:
        return False

    has_applicable_price_band = False

    for table in tables:
        if not isinstance(table, dict):
            return False

        min_price = table.get("min_price")
        max_price = table.get("max_price")
        tiers = table.get("tiers")

        if not _is_valid_numeric(min_price) or not _is_valid_numeric(max_price):
            return False
        if min_price < 0 or max_price < min_price:
            return False
        if not isinstance(tiers, list) or not tiers:
            return False

        if max_price == float("inf") or max_price > 78.99:
            has_applicable_price_band = True

        prev_weight = None
        for tier in tiers:
            if not isinstance(tier, dict):
                return False
            max_weight = tier.get("max_weight")
            price = tier.get("price")
            if not _is_valid_numeric(max_weight) or not _is_valid_numeric(price):
                return False
            if max_weight <= 0 and max_weight != float("inf"):
                return False
            if price < 0:
                return False
            if prev_weight is not None and max_weight < prev_weight:
                return False
            prev_weight = max_weight

    return has_applicable_price_band


def _normalize_text(value: str) -> str:
    text = (value or "").lower().strip()
    replacements = {
        "á": "a",
        "à": "a",
        "â": "a",
        "ã": "a",
        "é": "e",
        "ê": "e",
        "í": "i",
        "ó": "o",
        "ô": "o",
        "õ": "o",
        "ú": "u",
        "ç": "c",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return " ".join(text.split())


def _to_float(number_str: str) -> float:
    value = str(number_str or "").strip()
    if not value:
        return 0.0
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        value = value.replace(".", "").replace(",", ".")
    return float(value)


def _extract_numbers(text: str) -> List[float]:
    nums: List[float] = []
    for token in _NUMBER_RE.findall(text or ""):
        try:
            nums.append(_to_float(token))
        except Exception:
            continue
    return nums


def _parse_weight(weight_str: str) -> float:
    """Convert a weight range string to max weight (kg)."""
    raw = str(weight_str or "").strip()
    normalized = _normalize_text(raw)
    if not normalized:
        return 0.0

    if "mais de" in normalized or "maior que" in normalized:
        return float("inf")

    numbers = _extract_numbers(normalized)
    if not numbers:
        return 0.0

    # "De X a Y kg" -> use Y; "Ate X kg" -> use X
    max_val = numbers[1] if len(numbers) >= 2 else numbers[0]

    # Convert grams to kg only when unit is explicitly g (not kg)
    has_grams = bool(re.search(r"\bg\b", normalized)) and "kg" not in normalized
    if has_grams:
        return max_val / 1000.0

    return max_val


def _parse_price(price_str: str) -> float:
    """Convert 'R$ 44,90' to 44.90."""
    numbers = _extract_numbers(price_str)
    return numbers[0] if numbers else 0.0


def _parse_price_range_header(header_str: str) -> Optional[Tuple[float, float]]:
    text = _normalize_text(header_str).replace("*", "")
    numbers = _extract_numbers(text)
    if not numbers:
        return None

    if "a partir de" in text or "acima de" in text or "mais de" in text:
        return numbers[-1], float("inf")

    if " ate " in f" {text} " and len(numbers) == 1:
        return 0.0, numbers[0]

    if " a " in f" {text} " and len(numbers) >= 2:
        return numbers[0], numbers[1]

    if len(numbers) == 1:
        return numbers[0], float("inf")

    return None


def _parse_matrix_table(table) -> List[Dict[str, Any]]:
    rows = table.find_all("tr")
    if len(rows) < 2:
        return []

    header_cells = rows[0].find_all(["th", "td"])
    if len(header_cells) < 2:
        return []

    header_values = [c.get_text(" ", strip=True) for c in header_cells]
    if "peso" not in _normalize_text(header_values[0]):
        return []

    column_ranges: List[Optional[Tuple[float, float]]] = [
        _parse_price_range_header(header_text) for header_text in header_values[1:]
    ]

    per_range: List[Optional[Dict[str, Any]]] = []
    for parsed_range in column_ranges:
        if not parsed_range:
            per_range.append(None)
            continue
        min_price, max_price = parsed_range
        per_range.append({"min_price": min_price, "max_price": max_price, "tiers": []})

    for row in rows[1:]:
        cols = row.find_all(["th", "td"])
        if len(cols) < 2:
            continue
        values = [c.get_text(" ", strip=True) for c in cols]
        max_weight = _parse_weight(values[0])
        if max_weight <= 0 and max_weight != float("inf"):
            continue

        for col_idx, table_entry in enumerate(per_range, start=1):
            if table_entry is None:
                continue
            if col_idx >= len(values):
                continue
            price = _parse_price(values[col_idx])
            table_entry["tiers"].append({"max_weight": max_weight, "price": price})

    parsed: List[Dict[str, Any]] = []
    for table_entry in per_range:
        if not table_entry or not table_entry["tiers"]:
            continue
        table_entry["tiers"].sort(key=lambda item: item["max_weight"])
        parsed.append(table_entry)
    return parsed


async def _fetch_shipping_tables() -> List[Dict[str, Any]]:
    """Fetch and parse Mercado Livre shipping ranges by sale-price bands."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                ML_SHIPPING_URL,
                headers={"User-Agent": USER_AGENT},
                timeout=15.0,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.error("Error fetching Mercado Livre shipping table: %s", exc)
            raise MLShippingError(f"Communication failure: {exc}") from exc

    soup = BeautifulSoup(resp.text, "html.parser")
    parsed_tables: List[Dict[str, Any]] = []
    for table in soup.find_all("table"):
        parsed_tables.extend(_parse_matrix_table(table))

    parsed_tables.sort(
        key=lambda item: (
            float(item.get("min_price", 0.0)),
            float(item.get("max_price", float("inf"))),
        )
    )
    return parsed_tables


async def is_shipping_layout_valid() -> bool:
    """
    Validate if the Mercado Livre shipping page layout is still parseable.

    Returns:
        bool: True when parsed structure is compatible with the expected format.
    """
    now = datetime.now()
    need_refresh = (
        _shipping_cache["data"] is None
        or _shipping_cache["last_fetched"] is None
        or (now - _shipping_cache["last_fetched"]) > CACHE_TTL
    )

    if need_refresh:
        try:
            _shipping_cache["data"] = await _fetch_shipping_tables()
            _shipping_cache["last_fetched"] = now
        except MLShippingError as exc:
            logger.error("Unable to validate Mercado Livre shipping layout: %s", exc)
            return False
        except Exception as exc:
            logger.error("Unexpected error while validating shipping layout: %s", exc)
            return False

    tables = _shipping_cache.get("data") or []
    is_valid = _is_shipping_tables_layout_valid(tables)
    if not is_valid:
        logger.error("Mercado Livre shipping layout appears incompatible with parser.")
    return is_valid


async def get_shipping_cost(
    cost_price: float,
    weight_kg: float,
    reference_price: Optional[float] = None,
) -> float:
    """
    Dynamic shipping cost lookup using Mercado Livre table.

    Base sale price:
    - reference_price (if > 0) OR
    - cost_price * 2
    """
    base_price = (
        float(reference_price)
        if reference_price is not None and float(reference_price) > 0
        else (float(cost_price) * 2.0)
    )

    # Business rule: below threshold, shipping subsidy is not applied.
    if base_price <= 78.99:
        return 0.0

    now = datetime.now()
    if (
        _shipping_cache["data"] is None
        or _shipping_cache["last_fetched"] is None
        or (now - _shipping_cache["last_fetched"]) > CACHE_TTL
    ):
        logger.info("Refreshing Mercado Livre shipping ranges cache...")
        _shipping_cache["data"] = await _fetch_shipping_tables()
        _shipping_cache["last_fetched"] = now

    tables = _shipping_cache["data"] or []
    if not tables:
        logger.warning("Shipping table parse failed; returning 0.0 as fallback.")
        return 0.0

    target_table: Optional[Dict[str, Any]] = None
    for table in tables:
        if table["min_price"] <= base_price <= table["max_price"]:
            target_table = table
            break

    if target_table is None:
        # Final open-ended tier
        for table in tables:
            if table["max_price"] == float("inf") and base_price >= table["min_price"]:
                target_table = table
                break

    if not target_table:
        return 0.0

    target_weight = max(float(weight_kg or 0.0), 0.0)
    for tier in target_table["tiers"]:
        if target_weight <= tier["max_weight"]:
            return float(tier["price"])

    # If no upper bound matched, use the last tier as fallback.
    return float(target_table["tiers"][-1]["price"]) if target_table["tiers"] else 0.0
