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
                
                # 3. Mapear dados
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
