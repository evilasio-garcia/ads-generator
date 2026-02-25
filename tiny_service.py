# -*- coding: utf-8 -*-
"""
Tiny ERP Integration Service
Integração somente-leitura com Tiny ERP API para busca de produtos por SKU
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple
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
TINY_API_NFS_PESQUISA = f"{TINY_API_BASE}/notas.fiscais.pesquisa.php"
TINY_API_NFS_OBTER = f"{TINY_API_BASE}/nota.fiscal.obter.php"

from datetime import datetime, timedelta



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


def _log_safe_request(url: str, has_token: bool, **kwargs):
    """Log de requisição sem expor o token"""
    logger.info(f"Tiny API Request: {url}, authenticated: {has_token}, params: {list(kwargs.keys())}")


async def _search_in_date_window(client: httpx.AsyncClient, token: str, sku: str, data_inicial: str, data_final: str, timeout: float = 15.0) -> float:
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
            response = await client.post(TINY_API_NFS_PESQUISA, data=payload, timeout=timeout)
            if response.status_code != 200:
                break
                
            data = response.json()
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
                
                detalhe_resp = await client.post(TINY_API_NFS_OBTER, data=detalhe_payload, timeout=timeout)
                if detalhe_resp.status_code == 200:
                    detalhe_data = detalhe_resp.json()
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


async def _find_most_recent_purchase_cost(client: httpx.AsyncClient, token: str, sku: str, timeout: float = 15.0) -> float:
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
        custo = await _search_in_date_window(client, token, sku, str_inicio, str_fim, timeout)
        
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
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                TINY_SEARCH_URL,
                data=payload,
                timeout=10.0
            )
            
            if response.status_code != 200:
                logger.warning(f"Tiny API validation failed: HTTP {response.status_code}")
                return False, f"Erro HTTP {response.status_code}"
            
            data = response.json()
            
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
    
    sku = sku.strip()
    last_error = None
    total_attempts = max_retries + 1  # 1 + 1 = 2 tentativas totais
    
    # Retry com backoff exponencial
    for attempt in range(total_attempts):
        async with httpx.AsyncClient() as client:
            try:
                if attempt > 0:
                    delay = 2 ** attempt  # 2s, 4s, 8s...
                    logger.info(f"Tentativa {attempt + 1}/{total_attempts} após {delay}s")
                    await asyncio.sleep(delay)
            
                # 1. Pesquisar produto por SKU
                search_payload = {
                    'token': token,
                    'formato': 'JSON',
                    'pesquisa': sku
                }
                
                _log_safe_request(TINY_SEARCH_URL, has_token=True, pesquisa=sku)
                
                search_response = await client.post(
                    TINY_SEARCH_URL,
                    data=search_payload,
                    timeout=timeout
                )
                
                if search_response.status_code != 200:
                    raise TinyServiceError(f"Erro HTTP {search_response.status_code}")
                
                search_data = search_response.json()
            
                # Verificar resposta da busca
                if 'retorno' not in search_data:
                    raise TinyServiceError("Resposta inválida da API")
                
                retorno = search_data['retorno']
                status = retorno.get('status', '')
                
                # Verificar erros de autenticação
                if status == 'Erro':
                    erros = retorno.get('erros', [])
                    if erros:
                        erro_msg = erros[0].get('erro', 'Erro desconhecido')
                        if 'token' in erro_msg.lower() or 'autentica' in erro_msg.lower():
                            raise TinyAuthError(f"Token inválido: {erro_msg}")
                        raise TinyServiceError(erro_msg)
                
                # Verificar se encontrou produtos
                produtos = retorno.get('produtos', [])
                if not produtos:
                    raise TinyNotFoundError(f"SKU '{sku}' não encontrado no Tiny ERP")
                
                # Procurar produto com código exato (SKU pode retornar vários resultados parciais)
                produto_info = None
                for p in produtos:
                    prod = p.get('produto', {})
                    if prod.get('codigo', '').strip().upper() == sku.upper():
                        produto_info = prod
                        break
                
                # Se não encontrou match exato, usar o primeiro resultado
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
                
                get_response = await client.post(
                    TINY_GET_URL,
                    data=get_payload,
                    timeout=timeout
                )
                
                if get_response.status_code != 200:
                    raise TinyServiceError(f"Erro HTTP {get_response.status_code} ao obter produto")
                
                get_data = get_response.json()
                
                if 'retorno' not in get_data or 'produto' not in get_data['retorno']:
                    raise TinyServiceError("Dados do produto não encontrados")
                
                produto_completo = get_data['retorno']['produto']
                
                # 3. Adicionar Lógica Deep Search e Kit para 'preco_custo'
                # --- CENÁRIO A: É UM KIT ---
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
                            
                        # Buscar componente na API
                        comp_payload = {
                            "token": token,
                            "formato": "JSON",
                            "id": comp_id
                        }
                        comp_resp = await client.post(TINY_GET_URL, data=comp_payload, timeout=timeout)
                        if comp_resp.status_code == 200:
                            comp_data = comp_resp.json()
                            comp_full = comp_data.get("retorno", {}).get("produto", {})
                            comp_sku = comp_full.get("codigo", "")
                            
                            if comp_sku:
                                custo_unit = await _find_most_recent_purchase_cost(client, token, comp_sku, timeout)
                                if custo_unit == 0.0:
                                    try:
                                        custo_unit = float(str(comp_full.get("preco_custo")).replace(',', '.'))
                                    except (ValueError, TypeError):
                                        custo_unit = 0.0
                                custo_total_kit += (custo_unit * comp_qty)
                    final_cost = custo_total_kit
                else:
                    # --- CENÁRIO B: SIMPLES ---
                    final_cost = await _find_most_recent_purchase_cost(client, token, sku, timeout)
                    if final_cost == 0.0:
                        try:
                            final_cost = float(str(produto_completo.get("preco_custo")).replace(',', '.'))
                            logger.info(f"[{sku}] Fallback cadastro: R$ {final_cost:.4f}")
                        except (ValueError, TypeError):
                            final_cost = 0.0

                produto_completo['preco_custo_calculado'] = final_cost

                # 4. Mapear dados
                mapped_data = map_tiny_to_product_data(produto_completo)
                
                logger.info(f"Produto '{sku}' obtido com sucesso")
                return mapped_data
                
            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(f"Timeout na tentativa {attempt + 1}")
                if attempt == total_attempts - 1:
                    raise TinyTimeoutError(f"Timeout após {total_attempts} tentativas")
                continue
                
            except TinyNotFoundError as e:
                # Erros que não devem ter retry (não importa tentar, esse SKU não existe)
                raise e
                
            except TinyAuthError as e:
                # O token falhou nesse exato momento, vamos reiniciar a conexão via loop
                last_error = e
                logger.warning(f"Erro de autenticação (401) na tentativa {attempt + 1}, tentando novamente: {str(e)}")
                if attempt == total_attempts - 1:
                    raise e
                continue
                
            except Exception as e:
                last_error = e
                logger.error(f"Erro na tentativa {attempt + 1}: {str(e)}")
                if attempt == total_attempts - 1:
                    raise TinyServiceError(f"Falha após {total_attempts} tentativas: {str(e)}")
                continue
        
    # Se chegou aqui, esgotou as tentativas
    raise TinyServiceError(f"Falha após {total_attempts} tentativas: {str(last_error)}")


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
