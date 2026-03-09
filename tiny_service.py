# -*- coding: utf-8 -*-
"""
Tiny ERP Integration Service
Integração somente-leitura com Tiny ERP API para busca de produtos por SKU
"""

import asyncio
import logging
import json
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple
import httpx

# Configuração de logging estruturado
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# URLs da API Tiny
TINY_API_BASE = "https://api.tiny.com.br/api2"
TINY_SEARCH_URL = f"{TINY_API_BASE}/produtos.pesquisa.php"
TINY_GET_URL = f"{TINY_API_BASE}/produto.obter.php"
TINY_INCLUDE_URL = f"{TINY_API_BASE}/produto.incluir.php"
TINY_API_NFS_PESQUISA = f"{TINY_API_BASE}/notas.fiscais.pesquisa.php"
TINY_API_NFS_OBTER = f"{TINY_API_BASE}/nota.fiscal.obter.php"

from datetime import datetime, timedelta

async def _call_tiny_api(url: str, payload: Dict[str, Any], timeout: float = 15.0, max_retries: int = 2) -> Dict[str, Any]:
    """
    Realiza uma chamada para a API Tiny garantindo uma nova conexão e retentativas silenciosas.
    Implementa o requisito de 'matar a conexão ao fim e sempre usar uma nova'.
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            # Sempre usamos um novo cliente para garantir uma conexão fresca
            async with httpx.AsyncClient() as client:
                if attempt > 0:
                    await asyncio.sleep(attempt * 1.5) # backoff simples
                
                response = await client.post(url, data=payload, timeout=timeout)
                
                # Se o status code for 502, 503 ou 504 (erros de gateway/timeout do servidor), podemos retentar
                if response.status_code in [502, 503, 504] and attempt < max_retries:
                    continue
                    
                if response.status_code != 200:
                    status_code = int(response.status_code)
                    if status_code in (401, 403):
                        raise TinyAuthError(f"Status HTTP {status_code}")
                    if status_code == 404:
                        raise TinyNotFoundError("Status HTTP 404")
                    if status_code == 408:
                        raise TinyTimeoutError("Status HTTP 408")
                    if status_code == 429:
                        raise TinyRateLimitError("Status HTTP 429")
                    raise TinyServiceError(f"Status HTTP {status_code}")
                
                data = response.json()
                retorno = data.get("retorno", {})
                
                # Alguns erros do Tiny indicam instabilidades que podem ser sanadas com retry
                if retorno.get("status") == "Erro":
                    errors = retorno.get("erros", [])
                    if errors:
                        msg = errors[0].get("erro", "")
                        # Se for erro de limite de requisições ou instabilidade temporária, retenta
                        if "limite" in msg.lower() or "temporariamente" in msg.lower() or "overload" in msg.lower():
                            if attempt < max_retries:
                                continue
                
                return data
                
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            last_error = e
            logger.warning(f"Conexão Tiny falhou na tentativa {attempt+1}: {e}. Retentando com nova conexão...")
            if attempt < max_retries:
                continue
        except TinyServiceError:
            raise
        except Exception as e:
            last_error = e
            # Erros de aplicação não precisam de retry imediato de conexão
            raise TinyServiceError(f"Erro inesperado na API Tiny: {str(e)}")
            
    raise TinyServiceError(f"Falha ao conectar com Tiny ERP após {max_retries+1} tentativas: {str(last_error)}")



class TinyServiceError(Exception):
    """Base exception para erros do TinyService"""
    pass


class TinyAuthError(TinyServiceError):
    """Token inválido ou autenticação falhou"""
    pass


class TinyNotFoundError(TinyServiceError):
    """SKU não encontrado"""
    pass


class TinyTimeoutError(TinyServiceError):
    """Timeout na requisição"""
    pass


class TinyRateLimitError(TinyServiceError):
    """Limite de requisições temporariamente excedido no Tiny"""
    pass


class TinyConflictError(TinyServiceError):
    """Conflito de recurso no Tiny (ex.: SKU duplicado)."""
    pass


class TinyValidationError(TinyServiceError):
    """Erro de validação de negócio para integração Tiny."""

    def __init__(self, message: str, code: str = "validation_error"):
        super().__init__(message)
        self.code = str(code or "validation_error").strip() or "validation_error"


def _normalize_error_message(message: Optional[str]) -> str:
    return (message or "").strip().lower()


def _is_tiny_auth_message(message: Optional[str]) -> bool:
    msg = _normalize_error_message(message)
    if not msg:
        return False
    auth_markers = (
        "token",
        "autentica",
        "não autorizado",
        "nao autorizado",
        "unauthorized",
        "acesso negado",
        "credencial",
        "api key",
    )
    return any(marker in msg for marker in auth_markers)


def _is_tiny_transient_message(message: Optional[str]) -> bool:
    msg = _normalize_error_message(message)
    if not msg:
        return False
    transient_markers = (
        "limite",
        "temporari",
        "timeout",
        "timed out",
        "overload",
        "indispon",
        "status http 429",
        "status http 502",
        "status http 503",
        "status http 504",
    )
    return any(marker in msg for marker in transient_markers)


def _is_tiny_not_found_message(message: Optional[str]) -> bool:
    msg = _normalize_error_message(message)
    if not msg:
        return False
    if "retornou registros" in msg:
        return True
    not_found_markers = (
        "não retornou registros",
        "nao retornou registros",
        "n?o retornou registros",
        "não encontrado",
        "nao encontrado",
        "n?o encontrado",
        "nenhum registro",
        "sem registros",
        "produto não encontrado",
        "produto nao encontrado",
        "produto n?o encontrado",
    )
    return any(marker in msg for marker in not_found_markers)


def _log_safe_request(url: str, has_token: bool, **kwargs):
    """Log de requisição sem expor o token"""
    logger.info(f"Tiny API Request: {url}, authenticated: {has_token}, params: {list(kwargs.keys())}")


async def _search_in_date_window(token: str, sku: str, data_inicial: str, data_final: str, timeout: float = 15.0) -> float:
    """Busca notas fiscais APENAS dentro de uma janela de datas específica (Assíncrono)."""
    page_number = 1
    candidatos = []
    eh_o_fim = False

    while not eh_o_fim:
        payload = {
            "token": token,
            "formato": "JSON",
            "tipoNota": "E",  # Entrada
            "dataInicial": data_inicial,
            "dataFinal": data_final,
            "pagina": page_number
        }
        
        try:
            data = await _call_tiny_api(TINY_API_NFS_PESQUISA, payload, timeout=timeout)
            retorno = data.get("retorno", {})
            
            if retorno.get("status") != "OK":
                break
                
            total_pages = int(retorno.get('numero_paginas', 1))
            notas = retorno.get("notas_fiscais", [])
            
            if not notas:
                break
                
            for item in notas:
                nota_resumo = item.get("nota_fiscal", {})
                
                if nota_resumo.get("cliente", {}).get("tipo_pessoa") == 'F':
                    continue
                    
                id_nota = nota_resumo.get("id")
                
                # Busca detalhe da nota
                detalhe_payload = {
                    "token": token,
                    "formato": "JSON",
                    "id": id_nota
                }
                
                detalhe_data = await _call_tiny_api(TINY_API_NFS_OBTER, detalhe_payload, timeout=timeout)
                full_nota = detalhe_data.get("retorno", {}).get("nota_fiscal", {})
                itens = full_nota.get("itens", [])
                
                for line in itens:
                    prod = line.get("item", {})
                    if prod.get("codigo") == sku:
                        candidatos.append({
                            "nota_numero": nota_resumo.get("numero"),
                            "data_emissao": full_nota.get("data_emissao"),
                            "valor_produtos": full_nota.get("valor_produtos"),
                            "valor_faturado": full_nota.get("valor_faturado"),
                            "custo_unitario": prod.get("valor_unitario")
                        })
                        break
                            
            page_number += 1
            eh_o_fim = (page_number > total_pages)
            
        except Exception as e:
            logger.error(f"Erro em _search_in_date_window: {e}")
            break

    if candidatos:
        try:
            candidatos.sort(
                key=lambda x: datetime.strptime(x['data_emissao'], "%d/%m/%Y"),
                reverse=True
            )
            vencedor = candidatos[0]
            
            val_prod_nf = float(str(vencedor['valor_produtos']).replace(',', '.'))
            val_fat_nf = float(str(vencedor['valor_faturado']).replace(',', '.'))
            
            taxa_custos_adicionais = 0.0
            if val_prod_nf > 0:
                taxa_custos_adicionais = (val_fat_nf / val_prod_nf) - 1
                
            custo_unitario = float(str(vencedor['custo_unitario']).replace(',', '.'))
            custo_final = custo_unitario * (1 + taxa_custos_adicionais)
            
            logger.info(f"[{sku}] Deep Search: Custo encontrado na NF {vencedor['nota_numero']}: R$ {custo_final:.4f}")
            return round(custo_final, 4)
        except Exception as e:
            logger.error(f"[{sku}] Erro processando candidato vencedor: {e}")
            return 0.0
            
    return 0.0


async def _find_most_recent_purchase_cost(token: str, sku: str, timeout: float = 15.0) -> float:
    """Busca em Deep Search por notas fiscais de entrada assíncrono."""
    initial_lookback = 90
    deep_search_step = 30
    max_lookback_days = 730

    current_end_date = datetime.now()
    current_start_date = current_end_date - timedelta(days=initial_lookback)

    total_days_checked = initial_lookback
    attempt = 1
    str_inicio = current_start_date.strftime("%d/%m/%Y")
    str_fim = current_end_date.strftime("%d/%m/%Y")

    logger.info(f"[{sku}] Iniciando busca de custo (Janela {str_inicio} a {str_fim})")

    while total_days_checked <= max_lookback_days:
        custo = await _search_in_date_window(token, sku, str_inicio, str_fim, timeout)
        
        if custo > 0:
            return custo
            
        current_end_date = current_start_date - timedelta(days=1)
        current_start_date = current_end_date - timedelta(days=deep_search_step)
        
        total_days_checked += deep_search_step
        attempt += 1
        str_inicio = current_start_date.strftime("%d/%m/%Y")
        str_fim = current_end_date.strftime("%d/%m/%Y")
        
        if attempt > 1:
            logger.info(f"[{sku}] Recuando (Tentativa {attempt}: Janela {str_inicio} a {str_fim})")

    return 0.0



async def validate_token(token: str) -> Tuple[bool, Optional[str]]:
    """
    Valida se o token Tiny é válido fazendo uma requisição simples.
    
    Args:
        token: Token API do Tiny ERP
        
    Returns:
        Tuple[bool, Optional[str]]: (é_válido, mensagem_erro)
    """
    if not token or not token.strip():
        return False, "Token vazio"
    
    try:
        payload = {
            'token': token.strip(),
            'formato': 'JSON',
            'pesquisa': ''
        }
        
        _log_safe_request(TINY_SEARCH_URL, has_token=True, pesquisa='empty')
        
        data = await _call_tiny_api(TINY_SEARCH_URL, payload, timeout=10.0)
        
        if 'retorno' not in data:
            logger.warning("Tiny API validation: resposta inválida")
            return False, "Resposta inválida da API"
        
        status = data['retorno'].get('status', '')
        status_processamento = data['retorno'].get('status_processamento', '')
        
        if status == 'OK' or status_processamento == '3':
            logger.info("Token validado com sucesso")
            return True, None
        
        erro_msg = data['retorno'].get('erros', [{}])[0].get('erro', 'Token inválido')
        logger.warning(f"Tiny API validation failed: {erro_msg}")
        return False, erro_msg
        
    except httpx.TimeoutException:
        logger.error("Timeout ao validar token Tiny")
        return False, "Timeout na validação"
    except Exception as e:
        logger.error(f"Erro ao validar token: {str(e)}")
        return False, f"Erro: {str(e)}"


async def get_product_by_sku(
    token: str,
    sku: str,
    max_retries: int = 1,
    timeout: float = 15.0
) -> Dict[str, Any]:
    """
    Busca produto no Tiny ERP por SKU com retry exponencial.
    
    Args:
        token: Token API do Tiny
        sku: SKU do produto (código)
        max_retries: Número máximo de retentativas após primeira tentativa (padrão: 1 = 2 tentativas totais)
        timeout: Timeout em segundos (padrão: 15.0)
        
    Returns:
        Dict com dados do produto mapeados
        
    Raises:
        TinyAuthError: Token inválido
        TinyNotFoundError: SKU não encontrado
        TinyTimeoutError: Timeout nas requisições
        TinyServiceError: Outros erros
    """
    if not token or not token.strip():
        raise TinyAuthError("Token não fornecido")
    
    if not sku or not sku.strip():
        raise TinyNotFoundError("SKU não fornecido")
    
    token = token.strip()
    sku = sku.strip()
    
    # 1. Sanity Check Inicial (Válida Token e Conexão antes de começar)
    # Atende ao requisito de "fazer uma primeira verificação de sanidade"
    is_ok, error = await validate_token(token)
    if not is_ok:
        if _is_tiny_auth_message(error):
            raise TinyAuthError(f"Falha na sanidade da conexão/token: {error}")
        if _is_tiny_transient_message(error):
            raise TinyRateLimitError(f"Falha temporária na sanidade da conexão Tiny: {error}")
        raise TinyServiceError(f"Falha na sanidade da conexão Tiny: {error}")

    try:
        # 1. Pesquisar produto por SKU
        search_payload = {
            'token': token,
            'formato': 'JSON',
            'pesquisa': sku
        }
        
        _log_safe_request(TINY_SEARCH_URL, has_token=True, pesquisa=sku)
        search_data = await _call_tiny_api(TINY_SEARCH_URL, search_payload, timeout=timeout)
    
        # Verificar resposta da busca
        if 'retorno' not in search_data:
            raise TinyServiceError("Resposta inválida da API")
        
        retorno = search_data['retorno']
        status = retorno.get('status', '')
        
        # Verificar erros de autenticação (mesmo após sanity check, por segurança)
        if status == 'Erro':
            erros = retorno.get('erros', [])
            if erros:
                erro_msg = erros[0].get('erro', 'Erro desconhecido')
                if _is_tiny_auth_message(erro_msg):
                    raise TinyAuthError(f"Token inválido: {erro_msg}")
                if _is_tiny_not_found_message(erro_msg):
                    raise TinyNotFoundError(f"SKU '{sku}' não encontrado no Tiny ERP")
                if _is_tiny_transient_message(erro_msg):
                    raise TinyRateLimitError(erro_msg)
                raise TinyServiceError(erro_msg)
        
        # Verificar se encontrou produtos
        produtos = retorno.get('produtos', [])
        if not produtos:
            raise TinyNotFoundError(f"SKU '{sku}' não encontrado no Tiny ERP")
        
        # Procurar produto com código exato
        produto_info = None
        for p in produtos:
            prod = p.get('produto', {})
            if prod.get('codigo', '').strip().upper() == sku.upper():
                produto_info = prod
                break
        
        if not produto_info:
            produto_info = produtos[0].get('produto', {})
            logger.warning(f"SKU '{sku}' não encontrado exato, usando melhor match")
        
        # 2. Obter detalhes completos do produto
        produto_id = produto_info.get('id')
        if not produto_id:
            raise TinyServiceError("ID do produto não encontrado")
        
        get_payload = {
            'token': token,
            'formato': 'JSON',
            'id': produto_id
        }
        
        _log_safe_request(TINY_GET_URL, has_token=True, id=produto_id)
        get_data = await _call_tiny_api(TINY_GET_URL, get_payload, timeout=timeout)
        
        if 'retorno' not in get_data or 'produto' not in get_data['retorno']:
            raise TinyServiceError("Dados do produto não encontrados")
        
        produto_completo = get_data['retorno']['produto']
        
        # 3. Adicionar Lógica Deep Search e Kit
        final_cost = 0.0
        kit_items = produto_completo.get("kit", [])
        
        if kit_items:
            logger.info(f"[{sku}] Identificado como KIT. Calculando componentes...")
            custo_total_kit = 0.0
            for component in kit_items:
                item_data = component.get("item", {})
                comp_id = item_data.get("id_produto")
                
                qty_str = item_data.get("quantidade", 0)
                try:
                    comp_qty = float(str(qty_str).replace(',', '.'))
                except (ValueError, TypeError):
                    comp_qty = 0.0
                    
                # Buscar componente
                comp_payload = {
                    "token": token,
                    "formato": "JSON",
                    "id": comp_id
                }
                comp_data = await _call_tiny_api(TINY_GET_URL, comp_payload, timeout=timeout)
                comp_full = comp_data.get("retorno", {}).get("produto", {})
                comp_sku = comp_full.get("codigo", "")
                
                if comp_sku:
                    custo_unit = await _find_most_recent_purchase_cost(token, comp_sku, timeout)
                    if custo_unit == 0.0:
                        try:
                            custo_unit = float(str(comp_full.get("preco_custo")).replace(',', '.'))
                        except (ValueError, TypeError):
                            custo_unit = 0.0
                    custo_total_kit += (custo_unit * comp_qty)
            final_cost = custo_total_kit
        else:
            final_cost = await _find_most_recent_purchase_cost(token, sku, timeout)
            if final_cost == 0.0:
                try:
                    final_cost = float(str(produto_completo.get("preco_custo")).replace(',', '.'))
                    logger.info(f"[{sku}] Fallback cadastro: R$ {final_cost:.4f}")
                except (ValueError, TypeError):
                    final_cost = 0.0

        produto_completo['preco_custo_calculado'] = final_cost
        mapped_data = map_tiny_to_product_data(produto_completo)
        
        logger.info(f"Produto '{sku}' obtido com sucesso")
        return mapped_data
        
    except (TinyAuthError, TinyNotFoundError, TinyRateLimitError):
        raise
    except Exception as e:
        logger.error(f"Erro ao obter produto Tiny: {str(e)}")
        raise TinyServiceError(f"Falha na comunicação com Tiny ERP: {str(e)}")


def map_tiny_to_product_data(raw_product: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mapeia resposta da API Tiny para formato interno do app.
    
    Args:
        raw_product: Dados brutos do produto da API Tiny
        
    Returns:
        Dict com dados mapeados:
            - title: Nome do produto
            - sku: Código/SKU
            - gtin: GTIN/EAN
            - height_cm: Altura em cm
            - width_cm: Largura em cm
            - length_cm: Comprimento em cm
            - weight_kg: Peso em kg
            - cost_price: Preço de custo
            - list_price: Preço de lista/tabela
            - promo_price: Preço promocional
    """
    def safe_float(value, default=0.0):
        """Converte valor para float com fallback"""
        if value is None or value == '':
            return default
        try:
            return float(str(value).replace(',', '.'))
        except (ValueError, TypeError):
            return default
    
    def safe_str(value, default=''):
        """Converte valor para string com fallback"""
        if value is None:
            return default
        return str(value).strip()
    
    # Extrair campos básicos
    title = safe_str(raw_product.get('nome', ''))
    sku = safe_str(raw_product.get('codigo', ''))
    gtin = safe_str(raw_product.get('gtin', ''))
    unit = safe_str(raw_product.get('unidade', ''))
    # Categoria do produto no Tiny (pode ser objeto {id, descricao} ou string)
    _cat_raw = raw_product.get('categoria', '')
    if isinstance(_cat_raw, dict):
        categoria = safe_str(_cat_raw.get('descricao') or _cat_raw.get('nome', ''))
    else:
        categoria = safe_str(_cat_raw)
    
    # Dimensões (campos podem variar: altura_embalagem, largura_embalagem, etc)
    height_cm = safe_float(raw_product.get('alturaEmbalagem', 0))
    width_cm = safe_float(raw_product.get('larguraEmbalagem', 0))
    length_cm = safe_float(raw_product.get('comprimentoEmbalagem', 0))
    weight_kg = safe_float(raw_product.get('peso_bruto', 0))
    
    # Preços
    # Utilizar sempre o custo calculado (priorizando Notas Fiscais, seguindo no Kit, com fallback para o cadastro de produto)
    calculated_cost = raw_product.get('preco_custo_calculado')
    if calculated_cost is not None and calculated_cost > 0:
        cost_price = safe_float(calculated_cost)
    else:
        cost_price = safe_float(raw_product.get('preco_custo', 0))
        
    list_price = safe_float(raw_product.get('preco', 0))
    promo_price = safe_float(raw_product.get('preco_promocional', 0))
    
    mapped = {
        'title': title,
        'sku': sku,
        'gtin': gtin,
        'unit': unit,
        'categoria': categoria,
        'height_cm': height_cm,
        'width_cm': width_cm,
        'length_cm': length_cm,
        'weight_kg': weight_kg,
        'cost_price': cost_price,
        'list_price': list_price,
        'promo_price': promo_price,
        'raw_data': raw_product  # Manter dados brutos para debug
    }
    
    logger.info(f"Produto mapeado: {title} (SKU: {sku})")
    logger.debug(f"Dimensões: {height_cm}x{width_cm}x{length_cm} cm, {weight_kg} kg")
    
    return mapped


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return default


def _normalize_sku(value: Any) -> str:
    return str(value or "").strip().upper()


def _extract_tiny_error(retorno: Dict[str, Any]) -> str:
    erros = retorno.get("erros") or []
    if isinstance(erros, list) and erros:
        first = erros[0]
        if isinstance(first, dict):
            msg = first.get("erro")
            if msg:
                return str(msg).strip()
        if first:
            return str(first).strip()
    return "Erro desconhecido na API Tiny."


def _assert_tiny_ok_or_raise(retorno: Dict[str, Any]) -> None:
    status = str(retorno.get("status") or "").strip().upper()
    if status == "OK":
        return

    msg = _extract_tiny_error(retorno)
    if _is_tiny_auth_message(msg):
        raise TinyAuthError(msg)
    if _is_tiny_not_found_message(msg):
        raise TinyNotFoundError(msg)
    if _is_tiny_transient_message(msg):
        raise TinyRateLimitError(msg)
    if "ja cadastrado" in msg.lower() or "já cadastrado" in msg.lower() or "ja existe" in msg.lower():
        raise TinyConflictError(msg)
    raise TinyServiceError(msg)


async def _search_products_by_term(token: str, term: str, timeout: float = 15.0) -> List[Dict[str, Any]]:
    payload = {
        "token": token,
        "formato": "JSON",
        "pesquisa": str(term or "").strip(),
    }
    data = await _call_tiny_api(TINY_SEARCH_URL, payload, timeout=timeout)
    retorno = data.get("retorno") or {}
    _assert_tiny_ok_or_raise(retorno)
    products: List[Dict[str, Any]] = []
    for row in retorno.get("produtos") or []:
        prod = row.get("produto") if isinstance(row, dict) else None
        if isinstance(prod, dict):
            products.append(prod)
    return products


async def _get_product_full_by_id(token: str, product_id: Any, timeout: float = 15.0) -> Dict[str, Any]:
    payload = {
        "token": token,
        "formato": "JSON",
        "id": str(product_id or "").strip(),
    }
    data = await _call_tiny_api(TINY_GET_URL, payload, timeout=timeout)
    retorno = data.get("retorno") or {}
    _assert_tiny_ok_or_raise(retorno)
    product = retorno.get("produto")
    if not isinstance(product, dict):
        raise TinyServiceError("Resposta Tiny sem objeto de produto.")
    return product


async def _get_product_full_by_code_exact(token: str, code: str, timeout: float = 15.0) -> Optional[Dict[str, Any]]:
    code_norm = _normalize_sku(code)
    if not code_norm:
        return None

    try:
        products = await _search_products_by_term(token, code_norm, timeout=timeout)
    except TinyNotFoundError:
        return None
    except TinyServiceError as e:
        if "status http 404" in str(e).lower():
            return None
        raise
    exact = None
    for prod in products:
        if _normalize_sku(prod.get("codigo")) == code_norm:
            exact = prod
            break
    if exact is None:
        return None

    product_id = exact.get("id")
    if not product_id:
        return None
    try:
        return await _get_product_full_by_id(token, product_id, timeout=timeout)
    except TinyNotFoundError:
        return None
    except TinyServiceError as e:
        if "status http 404" in str(e).lower():
            return None
        raise


def _extract_structure_items(product_full: Dict[str, Any]) -> List[Dict[str, Any]]:
    structure_raw = []
    if isinstance(product_full.get("estrutura"), list):
        structure_raw = product_full.get("estrutura") or []
    elif isinstance(product_full.get("kit"), list):
        structure_raw = product_full.get("kit") or []

    items: List[Dict[str, Any]] = []
    for entry in structure_raw:
        if not isinstance(entry, dict):
            continue
        item = entry.get("item") if isinstance(entry.get("item"), dict) else entry
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "id_produto": str(item.get("id_produto") or item.get("id") or "").strip(),
                "codigo": _normalize_sku(item.get("codigo")),
                "quantidade": _safe_float(item.get("quantidade"), 0.0),
            }
        )
    return items


async def validate_kit_structure(
    token: str,
    product_full: Dict[str, Any],
    base_sku: str,
    expected_quantity: int,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    base_sku_norm = _normalize_sku(base_sku)
    expected_qty = int(expected_quantity or 0)
    class_raw = str(product_full.get("classe_produto") or product_full.get("classe") or "").strip().upper()
    is_kit_class = class_raw == "K"

    items = _extract_structure_items(product_full)
    sku_totals = defaultdict(float)
    component_skus: List[str] = []

    component_code_cache: Dict[str, str] = {}
    for item in items:
        comp_code = _normalize_sku(item.get("codigo"))
        comp_id = str(item.get("id_produto") or "").strip()
        if not comp_code and comp_id:
            if comp_id not in component_code_cache:
                try:
                    comp_full = await _get_product_full_by_id(token, comp_id, timeout=timeout)
                    component_code_cache[comp_id] = _normalize_sku(comp_full.get("codigo"))
                except TinyServiceError:
                    component_code_cache[comp_id] = ""
            comp_code = component_code_cache[comp_id]
        if not comp_code:
            continue
        qty = _safe_float(item.get("quantidade"), 0.0)
        sku_totals[comp_code] += qty
        component_skus.append(comp_code)

    unique_component_skus = sorted(set(component_skus))
    total_component_qty = _safe_float(sku_totals.get(base_sku_norm, 0.0), 0.0)
    only_base_sku = bool(unique_component_skus) and set(unique_component_skus) == {base_sku_norm}
    quantity_matches = abs(total_component_qty - float(expected_qty)) < 1e-9
    is_valid = is_kit_class and only_base_sku and quantity_matches

    return {
        "is_valid": is_valid,
        "is_kit_class": is_kit_class,
        "only_base_sku": only_base_sku,
        "quantity_matches": quantity_matches,
        "total_component_qty": total_component_qty,
        "component_skus": unique_component_skus,
    }


async def resolve_kit_candidate(
    token: str,
    base_sku: str,
    kit_quantity: int,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    base_sku_norm = _normalize_sku(base_sku)
    quantity = int(kit_quantity or 0)
    if not base_sku_norm:
        raise TinyValidationError("SKU base obrigatorio para resolver kit.", code="base_sku_required")
    if quantity < 2:
        raise TinyValidationError("Quantidade de kit invalida.", code="kit_quantity_invalid")

    candidates = [f"{base_sku_norm}CB{quantity}", f"{base_sku_norm}-CB{quantity}"]
    last_validation = None

    for candidate in candidates:
        product_full = await _get_product_full_by_code_exact(token, candidate, timeout=timeout)
        if not product_full:
            continue
        validation = await validate_kit_structure(
            token=token,
            product_full=product_full,
            base_sku=base_sku_norm,
            expected_quantity=quantity,
            timeout=timeout,
        )
        validation["candidate_sku"] = candidate
        last_validation = validation
        if validation.get("is_valid"):
            return {
                "status": "found",
                "resolved_sku": candidate,
                "searched_candidates": candidates,
                "create_available": False,
                "validation": validation,
                "message": f"Kit valido encontrado no SKU {candidate}.",
            }

    return {
        "status": "missing",
        "resolved_sku": None,
        "searched_candidates": candidates,
        "create_available": True,
        "validation": last_validation,
        "message": "Nenhum SKU de kit valido encontrado para esta quantidade.",
    }


UNIT_PLURAL_MAP = {
    "UN": "UNIDADES",
    "UND": "UNIDADES",
    "UNID": "UNIDADES",
    "UNIDADE": "UNIDADES",
    "PCT": "PACOTES",
    "PAC": "PACOTES",
    "PC": "PACOTES",
    "PACOTE": "PACOTES",
    "CX": "CAIXAS",
    "CAIXA": "CAIXAS",
    "FD": "FARDOS",
    "FARDO": "FARDOS",
    "KG": "QUILOS",
    "QUILO": "QUILOS",
    "G": "GRAMAS",
    "L": "LITROS",
    "LITRO": "LITROS",
    "LT": "LITROS",
    "ML": "MILILITROS",
    "MILILITRO": "MILILITROS",
}

DEFAULT_KIT_NAME_REPLACEMENTS = [
    {"from": " C/ ", "to": " COM "},
    {"from": " S/ ", "to": " SEM "},
    {"from": " PCT ", "to": " PACOTE "},
    {"from": " CX ", "to": " CAIXA "},
    {"from": " UNID ", "to": " UNIDADE "},
]


def _normalize_kit_name_replacements(
    replacements: Optional[List[Dict[str, Any]]],
) -> List[Tuple[str, str]]:
    source = replacements if isinstance(replacements, list) else DEFAULT_KIT_NAME_REPLACEMENTS
    normalized: List[Tuple[str, str]] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        from_text = str(item.get("from") or "")
        to_text = str(item.get("to") or "")
        if not from_text.strip():
            continue
        normalized.append((from_text, to_text))
    return normalized


def _apply_kit_name_replacements(
    raw_text: str,
    replacements: Optional[List[Dict[str, Any]]] = None,
) -> str:
    output = str(raw_text or "")
    for from_text, to_text in _normalize_kit_name_replacements(replacements):
        source = str(from_text or "")
        source_trimmed = source.strip()
        # Regras sem espaços e só com caracteres de palavra aplicam em "palavra inteira".
        # Isso evita corromper termos como "UNIDADES" quando a regra é "UNID".
        if source_trimmed and source_trimmed == source and re.fullmatch(r"[A-Za-z0-9_]+", source_trimmed):
            pattern = rf"(?<![A-Za-z0-9_]){re.escape(source_trimmed)}(?![A-Za-z0-9_])"
        else:
            pattern = re.escape(source)
        output = re.sub(pattern, to_text, output, flags=re.IGNORECASE)
    output = re.sub(r"\s{2,}", " ", output)
    return output.strip()


def _build_combo_name(
    *,
    base_product_full: Dict[str, Any],
    base_sku_norm: str,
    quantity: int,
    unit_plural: str,
    combo_name_override: Optional[str] = None,
    kit_name_replacements: Optional[List[Dict[str, Any]]] = None,
) -> str:
    override_name = str(combo_name_override or "").strip()
    if override_name:
        return override_name

    base_name = str(base_product_full.get("nome") or base_sku_norm).strip() or base_sku_norm
    normalized_base_name = _apply_kit_name_replacements(base_name, kit_name_replacements)
    return f"COMBO COM {quantity} {unit_plural}: {normalized_base_name or base_sku_norm}"


def resolve_combo_name_and_unit(
    *,
    base_product_full: Dict[str, Any],
    base_sku: str,
    kit_quantity: int,
    unit_plural_override: Optional[str] = None,
    combo_name_override: Optional[str] = None,
    kit_name_replacements: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, str]:
    base_sku_norm = _normalize_sku(base_sku)
    quantity = int(kit_quantity or 0)
    combo_name_value = str(combo_name_override or "").strip()
    if combo_name_value:
        unit_plural = str(unit_plural_override or "").strip().upper() or "UNIDADES"
    else:
        unit_plural = infer_unit_plural(base_product_full, override=unit_plural_override)

    combo_name = _build_combo_name(
        base_product_full=base_product_full,
        base_sku_norm=base_sku_norm,
        quantity=quantity,
        unit_plural=unit_plural,
        combo_name_override=combo_name_value or None,
        kit_name_replacements=kit_name_replacements,
    )
    return {
        "combo_name": combo_name,
        "unit_plural": unit_plural,
    }


def infer_unit_plural(product_full: Dict[str, Any], override: Optional[str] = None) -> str:
    if override and str(override).strip():
        return str(override).strip().upper()

    candidates = [
        product_full.get("unidade"),
        product_full.get("unidade_medida"),
        product_full.get("unidadeMedida"),
        product_full.get("un"),
    ]
    for value in candidates:
        key = _normalize_sku(value)
        if not key:
            continue
        if key in UNIT_PLURAL_MAP:
            return UNIT_PLURAL_MAP[key]
        if len(key) > 2:
            return key

    raise TinyValidationError(
        "Nao foi possivel inferir a unidade do produto base para montar o nome do combo.",
        code="unit_required",
    )


def _build_tiny_kit_payload(
    base_product_full: Dict[str, Any],
    base_sku: str,
    kit_quantity: int,
    unit_plural: str,
    announcement_price: Optional[float] = None,
    promotional_price: Optional[float] = 0.0,
    base_unit_override: Optional[str] = None,
    kit_weight_kg: Optional[float] = None,
    kit_height_cm: Optional[float] = None,
    kit_width_cm: Optional[float] = None,
    kit_length_cm: Optional[float] = None,
    kit_volumes: Optional[int] = 1,
    kit_description: Optional[str] = None,
    combo_name_override: Optional[str] = None,
    kit_name_replacements: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    base_sku_norm = _normalize_sku(base_sku)
    quantity = int(kit_quantity or 0)
    combo_sku = f"{base_sku_norm}CB{quantity}"
    base_name = str(base_product_full.get("nome") or base_sku_norm).strip()
    combo_name = _build_combo_name(
        base_product_full=base_product_full,
        base_sku_norm=base_sku_norm,
        quantity=quantity,
        unit_plural=unit_plural,
        combo_name_override=combo_name_override,
        kit_name_replacements=kit_name_replacements,
    )

    base_product_id = str(base_product_full.get("id") or "").strip()
    if not base_product_id:
        raise TinyServiceError("Produto base sem id no Tiny; nao foi possivel montar estrutura do kit.")

    unidade = str(base_unit_override or base_product_full.get("unidade") or "UN").strip() or "UN"
    origem = str(base_product_full.get("origem") or "0").strip()[:1] or "0"
    situacao = str(base_product_full.get("situacao") or "A").strip().upper()[:1] or "A"
    tipo = str(base_product_full.get("tipo") or "P").strip().upper()[:1] or "P"
    base_price = _safe_float(base_product_full.get("preco"), 0.0)
    base_promo_price = _safe_float(base_product_full.get("preco_promocional"), 0.0)
    base_cost_price = _safe_float(base_product_full.get("preco_custo"), 0.0)
    override_announce_price = _safe_float(announcement_price, 0.0)
    combo_price = round(
        (override_announce_price if override_announce_price > 0 else (base_price if base_price > 0 else base_cost_price if base_cost_price > 0 else 0.01) * quantity),
        2
    )
    override_promo_price = _safe_float(promotional_price, -1.0)
    if override_promo_price >= 0:
        combo_promo_price = round(override_promo_price, 2)
    else:
        combo_promo_price = round((base_promo_price if base_promo_price > 0 else combo_price / quantity) * quantity, 2)
    peso = _safe_float(kit_weight_kg, 0.0)
    altura = _safe_float(kit_height_cm, 0.0)
    largura = _safe_float(kit_width_cm, 0.0)
    comprimento = _safe_float(kit_length_cm, 0.0)
    volumes = _safe_int(kit_volumes, 1)
    descricao_complementar = str(kit_description or "").strip()

    payload: Dict[str, Any] = {
        "nome": combo_name,
        "codigo": combo_sku,
        "gtin": "",
        "classe_produto": "K",
        "unidade": unidade,
        "origem": origem,
        "situacao": situacao,
        "tipo": tipo,
        "preco": combo_price,
        "preco_promocional": combo_promo_price,
        "peso_bruto": peso if peso > 0 else 0.0,
        "peso_liquido": peso if peso > 0 else 0.0,
        "altura_embalagem": altura if altura > 0 else 0.0,
        "largura_embalagem": largura if largura > 0 else 0.0,
        "comprimento_embalagem": comprimento if comprimento > 0 else 0.0,
        "kit": [
            {
                "item": {
                    "id_produto": base_product_id,
                    "quantidade": quantity,
                }
            }
        ],
        "estrutura": [
            {
                "item": {
                    "id_produto": base_product_id,
                    "codigo": base_sku_norm,
                    "descricao": base_name,
                    "quantidade": quantity,
                }
            }
        ],
    }
    if descricao_complementar:
        payload["descricao_complementar"] = descricao_complementar

    for key in ("ncm", "marca", "cest", "codigo_fornecedor", "codigo_pelo_fornecedor"):
        if base_product_full.get(key) not in (None, ""):
            payload[key] = base_product_full.get(key)
    return payload


def _extract_include_record(retorno: Dict[str, Any]) -> Dict[str, Any]:
    registros = retorno.get("registros")
    if not isinstance(registros, list) or not registros:
        return {}
    first = registros[0]
    if isinstance(first, dict):
        registro = first.get("registro")
        if isinstance(registro, dict):
            return registro
    return {}


def _assert_include_record_ok_or_raise(retorno: Dict[str, Any]) -> Optional[str]:
    registro = _extract_include_record(retorno)
    if not registro:
        return None

    status = str(registro.get("status") or "").strip().upper()
    if status and status != "OK":
        erros = registro.get("erros") or []
        msg = ""
        if isinstance(erros, list) and erros:
            first = erros[0]
            if isinstance(first, dict):
                msg = str(first.get("erro") or "").strip()
            else:
                msg = str(first).strip()
        if not msg:
            msg = "Erro no registro de inclusão do produto."

        if _is_tiny_auth_message(msg):
            raise TinyAuthError(msg)
        if _is_tiny_not_found_message(msg):
            raise TinyNotFoundError(msg)
        if _is_tiny_transient_message(msg):
            raise TinyRateLimitError(msg)
        if "ja cadastrado" in msg.lower() or "já cadastrado" in msg.lower() or "ja existe" in msg.lower():
            raise TinyConflictError(msg)
        raise TinyServiceError(msg)

    product_id = str(registro.get("id") or "").strip()
    return product_id or None


def _is_promotional_price_required_message(message: str) -> bool:
    msg = _normalize_error_message(message)
    if not msg:
        return False
    promo_markers = (
        "preco promocional",
        "preço promocional",
    )
    required_markers = (
        "deve ser informado",
        "obrigatorio",
        "obrigatório",
        "invalido",
        "inválido",
    )
    return any(p in msg for p in promo_markers) and any(r in msg for r in required_markers)


async def create_kit_product(
    token: str,
    base_sku: str,
    kit_quantity: int,
    unit_plural_override: Optional[str] = None,
    combo_name_override: Optional[str] = None,
    kit_name_replacements: Optional[List[Dict[str, Any]]] = None,
    announcement_price: Optional[float] = None,
    promotional_price: Optional[float] = 0.0,
    base_unit_override: Optional[str] = None,
    kit_weight_kg: Optional[float] = None,
    kit_height_cm: Optional[float] = None,
    kit_width_cm: Optional[float] = None,
    kit_length_cm: Optional[float] = None,
    kit_volumes: Optional[int] = 1,
    kit_description: Optional[str] = None,
    timeout: float = 20.0,
) -> Dict[str, Any]:
    base_sku_norm = _normalize_sku(base_sku)
    quantity = int(kit_quantity or 0)
    if not base_sku_norm:
        raise TinyValidationError("SKU base obrigatorio para criar kit.", code="base_sku_required")
    if quantity < 2:
        raise TinyValidationError("Quantidade de kit invalida.", code="kit_quantity_invalid")

    combo_sku = f"{base_sku_norm}CB{quantity}"

    existing_combo = await _get_product_full_by_code_exact(token, combo_sku, timeout=timeout)
    if existing_combo:
        raise TinyConflictError(f"O codigo {combo_sku} ja existe no Tiny.")

    base_product_full = await _get_product_full_by_code_exact(token, base_sku_norm, timeout=timeout)
    if not base_product_full:
        raise TinyNotFoundError(f"SKU base '{base_sku_norm}' nao encontrado no Tiny ERP.")

    name_resolution = resolve_combo_name_and_unit(
        base_product_full=base_product_full,
        base_sku=base_sku_norm,
        kit_quantity=quantity,
        unit_plural_override=unit_plural_override,
        combo_name_override=combo_name_override,
        kit_name_replacements=kit_name_replacements,
    )
    combo_name_value = str(name_resolution.get("combo_name") or "").strip()
    unit_plural = str(name_resolution.get("unit_plural") or "").strip().upper() or "UNIDADES"

    product_payload = _build_tiny_kit_payload(
        base_product_full=base_product_full,
        base_sku=base_sku_norm,
        kit_quantity=quantity,
        unit_plural=unit_plural,
        announcement_price=announcement_price,
        promotional_price=promotional_price,
        base_unit_override=base_unit_override,
        kit_weight_kg=kit_weight_kg,
        kit_height_cm=kit_height_cm,
        kit_width_cm=kit_width_cm,
        kit_length_cm=kit_length_cm,
        kit_volumes=kit_volumes,
        kit_description=kit_description,
        combo_name_override=combo_name_value,
        kit_name_replacements=kit_name_replacements,
    )
    include_product_layout = {
        "produtos": [
            {
                "produto": {
                    "sequencia": "1",
                    **product_payload,
                }
            }
        ]
    }
    include_payload = {
        "token": token,
        "formato": "JSON",
        "produto": json.dumps(include_product_layout, ensure_ascii=False),
    }
    created_id = ""
    try:
        include_data = await _call_tiny_api(TINY_INCLUDE_URL, include_payload, timeout=timeout)
        retorno = include_data.get("retorno") or {}
        _assert_tiny_ok_or_raise(retorno)
        created_id = _assert_include_record_ok_or_raise(retorno) or ""
    except TinyServiceError as e:
        should_retry_with_announce_as_promo = (
            _is_promotional_price_required_message(str(e))
            and _safe_float(product_payload.get("preco"), 0.0) > 0
            and _safe_float(product_payload.get("preco_promocional"), 0.0) <= 0
        )
        if not should_retry_with_announce_as_promo:
            raise
        product_payload["preco_promocional"] = _safe_float(product_payload.get("preco"), 0.0)
        include_product_layout["produtos"][0]["produto"] = {
            "sequencia": "1",
            **product_payload,
        }
        include_payload["produto"] = json.dumps(include_product_layout, ensure_ascii=False)
        include_data = await _call_tiny_api(TINY_INCLUDE_URL, include_payload, timeout=timeout)
        retorno = include_data.get("retorno") or {}
        _assert_tiny_ok_or_raise(retorno)
        created_id = _assert_include_record_ok_or_raise(retorno) or ""
    if not created_id:
        confirmed = None
        for _ in range(4):
            confirmed = await _get_product_full_by_code_exact(token, combo_sku, timeout=timeout)
            if confirmed:
                break
            await asyncio.sleep(0.15)
        if not confirmed:
            raise TinyServiceError(
                "Tiny retornou OK na inclusao do KIT, mas o SKU nao foi encontrado na confirmacao pos-inclusao."
            )
        created_id = str(confirmed.get("id") or "").strip()
    validation = {
        "is_valid": True,
        "is_kit_class": True,
        "only_base_sku": True,
        "quantity_matches": True,
        "total_component_qty": float(quantity),
        "component_skus": [base_sku_norm],
    }

    return {
        "resolved_sku": combo_sku,
        "tiny_product_id": created_id or "",
        "validation": validation,
        "unit_plural": unit_plural,
        "payload": product_payload,
    }


async def suggest_kit_name(
    token: str,
    base_sku: str,
    kit_quantity: int,
    unit_plural_override: Optional[str] = None,
    combo_name_override: Optional[str] = None,
    kit_name_replacements: Optional[List[Dict[str, Any]]] = None,
    timeout: float = 20.0,
) -> Dict[str, str]:
    base_sku_norm = _normalize_sku(base_sku)
    quantity = int(kit_quantity or 0)
    if not base_sku_norm:
        raise TinyValidationError("SKU base obrigatorio para sugerir nome do kit.", code="base_sku_required")
    if quantity < 2:
        raise TinyValidationError("Quantidade de kit invalida.", code="kit_quantity_invalid")

    base_product_full = await _get_product_full_by_code_exact(token, base_sku_norm, timeout=timeout)
    if not base_product_full:
        raise TinyNotFoundError(f"SKU base '{base_sku_norm}' nao encontrado no Tiny ERP.")

    return resolve_combo_name_and_unit(
        base_product_full=base_product_full,
        base_sku=base_sku_norm,
        kit_quantity=quantity,
        unit_plural_override=unit_plural_override,
        combo_name_override=combo_name_override,
        kit_name_replacements=kit_name_replacements,
    )
