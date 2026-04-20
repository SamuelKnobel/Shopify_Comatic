"""
services/tax_mapper.py — Sophisticated VAT code determination.
Maps (Country, TaxRate, ProductType) -> ComaticVATCode.
"""
import json
from loguru import logger
from config import settings


class TaxMapper:
    """
    Handles the mapping of Shopify tax context to Comatic VAT codes.
    """

    def __init__(self) -> None:
        try:
            self.mapping = json.loads(settings.comatic_vat_mapping)
        except json.JSONDecodeError:
            logger.error("Failed to parse COMATIC_VAT_MAPPING from .env. Using empty mapping.")
            self.mapping = {}

    def get_vat_info(self, country_code: str, rate: float, product_type: str | None = None) -> tuple[str, str]:
        """
        Calculates the VAT code AND returns a human-readable reason for the choice.
        Returns: (code, reason)
        """
        country_map = self.mapping.get(country_code)
        if not country_map:
            return settings.comatic_default_vat_code, f"Fallback: No mapping for country {country_code}"

        # Determine category based on rate heuristics
        category = "Standard"
        if country_code == "CH":
            if abs(rate - 0.081) < 0.005: category = "Standard"
            elif abs(rate - 0.026) < 0.005: category = "Reduced"
        elif country_code == "DE":
            if abs(rate - 0.19) < 0.005: category = "Standard"
            elif abs(rate - 0.07) < 0.005: category = "Reduced"
            
        code = country_map.get(category)
        if not code:
            # Try a direct rate match as a string key just in case
            code = country_map.get(str(rate))
            
        if not code:
            return settings.comatic_default_vat_code, f"Fallback: {country_code} ({rate*100}%) matched no category"

        reason = f"{country_code} {category} Rate ({rate*100}%)"
        return str(code), reason

    def get_vat_code(self, country_code: str, rate: float, product_type: str | None = None) -> str:
        """Original signature for backward compatibility."""
        code, _ = self.get_vat_info(country_code, rate, product_type)
        return code

# Global singleton
tax_mapper = TaxMapper()
