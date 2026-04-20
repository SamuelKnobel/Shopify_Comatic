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

    def get_vat_code(self, country_code: str, rate: float, product_type: str | None = None) -> str:
        """
        Sophisticated lookup for the Comatic VAT code.
        
        Logic:
        1. Check if the country exists in the mapping.
        2. Identify the category derived from the rate (Standard vs Reduced).
           - This handles the (Suplements vs Cosmetics) case automatically because 
             Shopify calculates different rates for them.
        3. Fallback to default if no match.
        """
        country_map = self.mapping.get(country_code)
        if not country_map:
            logger.debug("No VAT mapping for country {cc}. Using default.", cc=country_code)
            return settings.comatic_default_vat_code

        # Heuristic for Standard vs Reduced (can be refined or made explicit in config)
        # For simplicity, we compare the rate against common thresholds.
        # But a better way is to look up the rate directly in the mapping keys.
        
        # We'll search for the closest rate key in the mapping for that country
        category = "Standard"
        # Example dummy mapping in .env: {"CH": {"Standard": "NN", "Reduced": "HB"}}
        
        # Determine category based on common rates if not explicitly keyed by float
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
            code = settings.comatic_default_vat_code
            logger.warning(
                "TaxMapper: No code found for {cc} rate={r} cat={cat}. Fallback={f}",
                cc=country_code, r=rate, cat=category, f=code
            )
        else:
            logger.debug(
                "TaxMapper: {cc} rate={r} -> {code} ({cat})",
                cc=country_code, r=rate, code=code, cat=category
            )
            
        return str(code)

# Global singleton
tax_mapper = TaxMapper()
