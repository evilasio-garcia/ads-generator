from typing import Dict
from pricing.interface import IPriceCalculator
from pricing.calculators import (
    MercadoLivrePriceCalculator,
    ShopeePriceCalculator,
    AmazonBRPriceCalculator,
    SheinPriceCalculator,
    MagaluPriceCalculator,
    EcommercePriceCalculator,
    TelemarketingPriceCalculator,
)


class PriceCalculatorFactory:
    """
    Factory para instanciar calculadoras de preço por canal.
    
    Usa mapeamento centralizado channel -> classe para garantir
    consistência e facilitar manutenção.
    """
    
    # Mapeamento canônico: channel -> Calculator class
    _CALCULATORS: Dict[str, type] = {
        "mercadolivre": MercadoLivrePriceCalculator,
        "shopee": ShopeePriceCalculator,
        "amazon": AmazonBRPriceCalculator,
        "shein": SheinPriceCalculator,
        "magalu": MagaluPriceCalculator,
        "ecommerce": EcommercePriceCalculator,
        "telemarketing": TelemarketingPriceCalculator,
    }
    
    @classmethod
    def get(cls, channel: str) -> IPriceCalculator:
        """
        Retorna a calculadora apropriada para o canal especificado.
        
        Args:
            channel: Nome do canal (case-insensitive)
            
        Returns:
            Instância de IPriceCalculator
            
        Raises:
            ValueError: Se o canal não for suportado
        """
        channel_lower = channel.lower().strip()
        
        calculator_class = cls._CALCULATORS.get(channel_lower)
        
        if not calculator_class:
            supported = ", ".join(cls._CALCULATORS.keys())
            raise ValueError(
                f"Canal '{channel}' não suportado. "
                f"Canais disponíveis: {supported}"
            )
        
        return calculator_class()
    
    @classmethod
    def get_supported_channels(cls) -> list:
        """Retorna lista de canais suportados"""
        return list(cls._CALCULATORS.keys())
    
    @classmethod
    def is_supported(cls, channel: str) -> bool:
        """Verifica se um canal é suportado"""
        return channel.lower().strip() in cls._CALCULATORS
