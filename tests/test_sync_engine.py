"""
tests/test_sync_engine.py — Updated tests for the Accountant Precision sync engine.
"""
import pytest
from datetime import datetime, timezone
from services.tax_mapper import tax_mapper
from services.sync_engine import _allocate_discounts, _build_invoice_rows
from models.shopify import ShopifyOrder, ShopifyLineItem, ShopifyShippingLine

@pytest.fixture
def mock_order():
    return ShopifyOrder(
        id=123,
        name="#1001",
        email="test@example.com",
        created_at=datetime.now(timezone.utc),
        financial_status="paid",
        currency="CHF",
        total_price="100.00",
        total_discounts="10.00",
        line_items=[
            ShopifyLineItem(id=1, title="Item 1", quantity=1, price="60.00", tax_lines=[{"rate": 0.081}]),
            ShopifyLineItem(id=2, title="Item 2", quantity=1, price="40.00", tax_lines=[{"rate": 0.026}]),
        ],
        shipping_lines=[
            ShopifyShippingLine(title="Post", price="10.00", tax_lines=[{"rate": 0.081}])
        ]
    )

def test_tax_mapper_ch_rates():
    # Test CH mapping logic
    assert tax_mapper.get_vat_code("CH", 0.081) == "NN"
    assert tax_mapper.get_vat_code("CH", 0.026) == "HB"
    assert tax_mapper.get_vat_code("CH", 0.0) == "NN" # fallback to default

def test_discount_allocation(mock_order):
    allocations = _allocate_discounts(mock_order)
    # Total lines = 100. Total discount = 10.
    # Item 1 (60%) -> 6.0
    # Item 2 (40%) -> 4.0
    assert allocations[1] == 6.0
    assert allocations[2] == 4.0

@pytest.mark.asyncio
async def test_build_invoice_rows_includes_shipping(mock_order):
    from unittest.mock import MagicMock
    mock_shopify = MagicMock()
    # Mock product type retrieval
    mock_shopify.get_product.return_value = {"product_type": "Cosmetics"}
    
    rows = await _build_invoice_rows(mock_order, mock_shopify)
    
    # 2 items + 1 shipping = 3 rows
    assert len(rows) == 3
    
    # Item 1
    assert rows[0].VatCode == "NN"
    assert rows[0].Discount == 6.0
    
    # Item 2
    assert rows[1].VatCode == "HB"
    assert rows[1].Discount == 4.0
    
    # Shipping
    assert rows[2].Description == "Shipping: Post"
    assert rows[2].VatCode == "NN"
