import pytest
from services.tax_mapper import TaxMapper

def test_tax_mapper_reasoning_standard():
    mapper = TaxMapper()
    # Test CH Standard
    code, reason = mapper.get_vat_info("CH", 0.081)
    assert code == "NN"
    assert "CH" in reason
    assert "Standard" in reason

def test_tax_mapper_reasoning_reduced():
    mapper = TaxMapper()
    # Test CH Reduced
    code, reason = mapper.get_vat_info("CH", 0.026)
    assert code == "HB"
    assert "Reduced" in reason

def test_tax_mapper_reasoning_fallback():
    mapper = TaxMapper()
    # Unknown country
    code, reason = mapper.get_vat_info("ZZ", 0.05)
    assert code == "NN"
    assert "Fallback" in reason
