"""
tests/test_sync_engine.py — Unit tests for core sync logic.

Uses respx to mock httpx calls (no real API calls during testing).
"""
import pytest
import pytest_asyncio
import respx
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

from models.shopify import ShopifyOrder, ShopifyLineItem, ShopifyCustomer, ShopifyAddress


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def make_order(
    order_id: int = 1001,
    financial_status: str = "paid",
    gateway: str = "stripe",
    email: str = "test@example.com",
) -> ShopifyOrder:
    return ShopifyOrder(
        id=order_id,
        name=f"#{order_id}",
        email=email,
        created_at=datetime.now(timezone.utc),
        financial_status=financial_status,
        payment_gateway=gateway,
        currency="CHF",
        total_price="99.00",
        customer=ShopifyCustomer(
            id=5000,
            email=email,
            first_name="Test",
            last_name="User",
        ),
        billing_address=ShopifyAddress(
            first_name="Test",
            last_name="User",
            address1="Hauptstrasse 1",
            city="Zürich",
            zip="8001",
            country="Switzerland",
            country_code="CH",
        ),
        line_items=[
            ShopifyLineItem(
                id=9001,
                title="Test Product",
                quantity=1,
                price="99.00",
                sku="SKU-001",
                taxable=True,
                tax_lines=[{"rate": 0.081, "title": "MwSt"}],
            )
        ],
    )


# ─── VAT mapping tests ─────────────────────────────────────────────────────────

def test_map_vat_code_ch_standard():
    from services.sync_engine import map_vat_code
    code = map_vat_code("CH", 0.081)
    assert code == "NN"


def test_map_vat_code_ch_reduced():
    from services.sync_engine import map_vat_code
    code = map_vat_code("CH", 0.026)
    assert code == "HB"


def test_map_vat_code_ch_zero():
    from services.sync_engine import map_vat_code
    code = map_vat_code("CH", 0.0)
    assert code == "US"


def test_map_vat_code_unknown_returns_default():
    from services.sync_engine import map_vat_code
    code = map_vat_code("US", 0.07)
    # Should return whatever is in settings.comatic_default_vat_code
    assert isinstance(code, str)
    assert len(code) > 0


# ─── Build invoice tests ───────────────────────────────────────────────────────

def test_build_invoice_paid_sets_open_position_false():
    from services.sync_engine import _build_invoice
    order = make_order(financial_status="paid")
    invoice = _build_invoice(order, address_id=42, is_paid=True)
    assert invoice.IsOpenPosition is False
    assert invoice.AddressId == 42
    assert invoice.ExternalId == "1001"


def test_build_invoice_pending_sets_open_position_true():
    from services.sync_engine import _build_invoice
    order = make_order(financial_status="pending", gateway="manual")
    invoice = _build_invoice(order, address_id=42, is_paid=False)
    assert invoice.IsOpenPosition is True


def test_build_invoice_has_detail_rows():
    from services.sync_engine import _build_invoice
    order = make_order()
    invoice = _build_invoice(order, address_id=1, is_paid=True)
    assert len(invoice.DetailRows) == 1
    row = invoice.DetailRows[0]
    assert row.Description == "Test Product"
    assert row.Quantity == 1.0
    assert row.VatCode == "NN"  # 8.1% → NN for CH


# ─── Deduplication guard ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_already_synced_order_is_skipped():
    """Orders already in DB with sync_status='synced' must be skipped, not reprocessed."""
    import tempfile, os
    from database import init_db, mark_order_synced, is_order_synced

    # Use a temp DB for isolation
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    with patch("config.settings.sqlite_db_path", db_path):
        from importlib import reload
        import database
        reload(database)

        await database.init_db()
        await database.mark_order_synced(
            shopify_order_id="1001",
            shopify_order_name="#1001",
            customer_email="test@example.com",
            financial_status="paid",
            payment_gateway="stripe",
            total_price=99.0,
            currency="CHF",
            comatic_invoice_id=99,
            comatic_address_id=42,
        )
        assert await database.is_order_synced("1001") is True
        assert await database.is_order_synced("9999") is False

    os.unlink(db_path)


@pytest.mark.asyncio
async def test_failed_order_is_not_marked_synced():
    """If Comatic call fails, order must NOT be synced → it stays retryable."""
    import tempfile, os

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    with patch("config.settings.sqlite_db_path", db_path):
        from importlib import reload
        import database
        reload(database)

        await database.init_db()
        await database.mark_order_failed("2002", "Comatic returned 500")
        assert await database.is_order_synced("2002") is False  # Must NOT be synced

    os.unlink(db_path)
