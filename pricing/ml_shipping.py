"""
Módulo responsável por fazer o scrape das faixas de frete
"""
import re
import logging
from typing import Dict, List, Optional, Any
import httpx
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# Configurações do Scraper
ML_SHIPPING_URL = "https://www.mercadolivre.com.br/ajuda/40538"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Cache
_shipping_cache: Dict[str, Any] = {
    "data": None,
    "last_fetched": None
}
CACHE_TTL = timedelta(hours=12)

logger = logging.getLogger(__name__)


class MLShippingError(Exception):
    pass


def _parse_weight(weight_str: str) -> float:
    """Converte string de peso ('De 5 kg a 9 kg', 'Até 300 g', 'Maior que 150 kg') para um float do peso máximo da faixa em kg."""
    w = weight_str.lower().strip()
    
    # "Até 300 g", "Até 5 kg"
    if "até" in w:
        val_str = re.search(r'\d+', w).group()
        val = float(val_str)
        if "g" in w and "kg" not in w:
            return val / 1000.0
        return val
        
    # "Maior que 150 kg"
    if "maior" in w:
        val_str = re.search(r'\d+', w).group()
        return float('inf')
        
    # "De X kg a Y kg", "De X g a Y g"
    matches = re.findall(r'\d+', w)
    if len(matches) >= 2:
        val = float(matches[1]) # Pega o teto da faixa
        if "g" in w and "kg" not in w.split("a")[1]: # a segunda parte define a métrica do teto
            return val / 1000.0
        return val
        
    return 0.0


def _parse_price(price_str: str) -> float:
    """Converte 'R$ 44,90' para 44.90"""
    p = price_str.lower().replace("r$", "").strip().replace(".", "").replace(",", ".")
    try:
        return float(p)
    except Exception:
        return 0.0


async def _fetch_shipping_tables() -> List[Dict]:
    """
    Busca e parseia as tabelas de frete da página oficial do ML.
    Retorna uma lista de coleções contendo faixas de preço e limites de peso.
    """
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(ML_SHIPPING_URL, headers={"User-Agent": USER_AGENT}, timeout=15.0)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Erro ao buscar tabelas do ML: {e}")
            raise MLShippingError(f"Falha de comunicação: {e}")

    soup = BeautifulSoup(resp.text, 'html.parser')
    tables = soup.find_all('table')
    
    parsed_tables = []
    
    # As tabelas costumam seguir a ordem: <79, 79-99.99, 100-119.99, 120-149.99, 150-199.99, >=200
    # Precisamos inspecionar os headers e extrair os limites
    for t in tables:
        rows = t.find_all('tr')
        if not rows: continue
        
        # Pega a primeira linha (cabeçalho) para descobrir do que se trata
        header_cols = rows[0].find_all(['th', 'td'])
        title = " ".join([c.get_text(strip=True) for c in header_cols])
        
        # Só parseia tabelas que comecem com "Peso" nas colunas e que listem "Produtos novos" ou algo assim
        if "Peso" not in title and "Produtos" not in title:
            continue
            
        # Determinar os limites de preço que esta tabela cobre (Base de Preço)
        min_price = 0.0
        max_price = float('inf')
        
        # Tabela 0: menos de R$ 79
        if "menos de" in title.lower() and "79" in title:
            max_price = 78.99
        elif "a" in title and ("r$" in title.lower() or "rs" in title.lower()):
            # Ex: "Produtos novos de R$ 79 a R$ 99,99"
            matches = re.findall(r'\d+[.,]?\d*', title.replace(".", "")) # Remove os . antes para nao dar pau com milhar
            if len(matches) >= 2:
                # O Regex ainda acha ",". Trocar para ponto.
                min_price = float(matches[0].replace(",", "."))
                max_price = float(matches[1].replace(",", "."))
        elif "mais de" in title.lower() or "maior que" in title.lower():
            matches = re.findall(r'\d+[.,]?\d*', title.replace(".", ""))
            if matches:
                min_price = float(matches[0].replace(",", "."))
                
        # Parseia as faixas de peso
        weight_tiers = []
        for row in rows[1:]: # Pula a header
            cols = row.find_all('td')
            if len(cols) >= 2:
                weight_desc = cols[0].get_text(strip=True)
                price_desc = cols[1].get_text(strip=True)
                
                max_weight = _parse_weight(weight_desc)
                value = _parse_price(price_desc)
                
                weight_tiers.append({
                    "max_weight": max_weight,
                    "price": value
                })
                
        # Ordenar weight tiers para busca facilitada depois
        weight_tiers.sort(key=lambda x: x["max_weight"])
        
        parsed_tables.append({
            "min_price": min_price,
            "max_price": max_price,
            "tiers": weight_tiers
        })
        
    return parsed_tables


async def get_shipping_cost(cost_price: float, weight_kg: float) -> float:
    """
    Retorna o custo do frete dinamicamente baseado na regra:
    Preço Base de Venda = Custo Produto * 2.
    Se Base <= 78.99 -> Frete R$ 0.00
    Senão -> Procura tabela e pega a faixa correspondente de peso.
    """
    base_price = cost_price * 2.0
    
    if base_price <= 78.99:
        return 0.0
        
    # Verificar cache
    now = datetime.now()
    if _shipping_cache["data"] is None or _shipping_cache["last_fetched"] is None or \
       (now - _shipping_cache["last_fetched"]) > CACHE_TTL:
        logger.info("Buscando dados atualizados de faixas de frete ML...")
        data = await _fetch_shipping_tables()
        _shipping_cache["data"] = data
        _shipping_cache["last_fetched"] = now
        
    tables = _shipping_cache["data"]
    
    if not tables:
        logger.warning("Falha ao usar envio dinamico (Tabelas não parseadas). Usando fallback manual ou 0.0.")
        return 0.0
        
    # Encontra a tabela de preço
    target_table = None
    for t in tables:
        if t["min_price"] <= base_price <= t["max_price"]:
            target_table = t
            break
            
    if target_table is None:
        # Se ultrapassou o limite superior da ultima tabela (ex: > 300 reais, mas a ultima era preco max infinito)
        # Tenta achar a tabela com max_price == inf
        for t in tables:
            if base_price >= t["min_price"] and t["max_price"] == float('inf'):
                target_table = t
                break
                
    if not target_table:
        return 0.0
        
    # Encontra a faixa de peso
    shipping_cost = 0.0
    for w in target_table["tiers"]:
        if weight_kg <= w["max_weight"]:
            shipping_cost = w["price"]
            break
            
    # Se passou do limite (ex peso 200kg > infinito)
    if shipping_cost == 0.0 and len(target_table["tiers"]) > 0:
        shipping_cost = target_table["tiers"][-1]["price"]
        
    return shipping_cost
