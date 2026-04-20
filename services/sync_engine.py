"""
services/sync_engine.py — Orchestrates the full Shopify → Comatic sync pipeline.
REWRITTEN for Accountant Precision: Handles mixed VAT, category cross-checks,
shipping items, discount allocation, and document storage.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from config import settings
from database import (
    finish_sync_run,
    is_order_synced,
    mark_order_failed,
    mark_order_synced,
    start_sync_run,
)
from models.comatic import (
    ComaticAddressCreate,
    ComaticInvoiceCreate,
    ComaticInvoiceDetailRow,
    ComaticPaymentOnAccountCreate,
)
from models.shopify import ShopifyOrder, ShopifyLineItem, ShopifyShippingLine
from services.comatic_client import ComaticAPIError, ComaticClient
from services.shopify_client import ShopifyClient
from services.tax_mapper import tax_mapper


# ─────────────────────────────────────────────────────────────────────────────
# Accountant Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _allocate_discounts(order: ShopifyOrder) -> dict[int, float]:
    """
    Distribute order-level discounts proportionally to line items.
    Returns a map of {line_item_id: discount_amount}.
    """
    total_discount = float(order.total_discounts or 0)
    if total_discount <= 0:
        return {}

    line_total_before_discount = sum(i.total_price_float for i in order.line_items)
    if line_total_before_discount <= 0:
        return {}

    allocations = {}
    for item in order.line_items:
        share = item.total_price_float / line_total_before_discount
        allocations[item.id] = round(total_discount * share, 2)
        
    return allocations


# ─────────────────────────────────────────────────────────────────────────────
# Per-order processing
# ─────────────────────────────────────────────────────────────────────────────

def _build_address(order: ShopifyOrder) -> ComaticAddressCreate:
    """Construct a Comatic address body from a Shopify order."""
    billing = order.billing_address
    currency = order.currency or "CHF"
    country_code = order.country_code

    lang_map = {"CH": "de-ch", "DE": "de-de", "AT": "de-at", "FR": "fr-fr"}
    lang_code = lang_map.get(country_code, "de-ch")

    return ComaticAddressCreate(
        Salutation="",
        Firstname=order.customer_first_name or "Unknown",
        Lastname=order.customer_last_name or "Unknown",
        Address01=billing.address1 if billing else None,
        Address02=billing.address2 if billing else None,
        Postalcode=billing.zip if billing else None,
        City=(billing.city if billing else None) or "Unknown",
        Region=billing.province if billing else None,
        CountryCode=country_code,
        LanguageCode=lang_code,
        Email=order.customer_email,
        Telephone01=(billing.phone if billing else None),
        CurrencyCode=currency[:3],
        AddressStatus="Active",
        AddressType="Debtor",
        DefaultSalesConditionCode=settings.comatic_default_sales_condition[:2],
        DefaultPurchaseConditionCode=settings.comatic_default_purchase_condition[:2],
        ExternalId=str(order.customer.id) if order.customer else None,
    )


async def _build_invoice_rows(
    order: ShopifyOrder, 
    shopify: ShopifyClient
) -> list[ComaticInvoiceDetailRow]:
    """
    Build precision invoice rows including products (with category check) and shipping.
    """
    country_code = order.country_code
    rows: list[ComaticInvoiceDetailRow] = []
    
    # 1. Allocate discounts
    discount_map = _allocate_discounts(order)
    
    # 2. Add Line Items
    for item in order.line_items:
        # Fetch product details for category cross-check
        product_type = None
        if item.product_id:
            try:
                product = await shopify.get_product(item.product_id)
                product_type = product.get("product_type")
            except Exception as e:
                logger.warning("Could not fetch product {id} details: {err}", id=item.product_id, err=e)

        rate = item.effective_tax_rate
        vat_code = tax_mapper.get_vat_code(country_code, rate, product_type)
        
        # Calculate line discount (if any)
        discount_amount = discount_map.get(item.id, 0.0)
        
        rows.append(
            ComaticInvoiceDetailRow(
                Description=f"{item.title} ({product_type})" if product_type else item.title,
                Specification=item.sku,
                Quantity=float(item.quantity),
                SingleAmount=item.unit_price_float,
                TotalAmount=item.total_price_float,
                Discount=discount_amount, # Comatic expects amount or %? Usually amount in this context.
                VatCode=vat_code,
                QuantityUnit="Stk",
                ExternalId=str(item.id),
            )
        )

    # 3. Add Shipping Lines
    for ship in order.shipping_lines:
        rate = ship.effective_tax_rate
        vat_code = tax_mapper.get_vat_code(country_code, rate, "Shipping")
        
        rows.append(
            ComaticInvoiceDetailRow(
                ArticleId=settings.comatic_shipping_article_id,
                Description=f"Shipping: {ship.title}",
                Quantity=1.0,
                SingleAmount=ship.price_float,
                TotalAmount=ship.price_float,
                VatCode=vat_code,
                QuantityUnit="Service",
                ExternalId=f"ship_{order.id}",
            )
        )

    return rows


async def _process_order(
    order: ShopifyOrder,
    shopify: ShopifyClient,
    comatic: ComaticClient,
) -> bool:
    """
    Process one Shopify order through the precision sync pipeline.
    """
    order_id = str(order.id)
    logger.info("Syncing order {name} (Accountant Precision)...", name=order.name)

    email = order.customer_email
    is_paid = order.financial_status == "paid"

    try:
        # ── Step 1: Document Storage ──────────────────────────────────────────
        if is_paid:
            await shopify.download_order_document(order.id)

        # ── Step 2: Customer ──────────────────────────────────────────────────
        comatic_address = None
        if email:
            comatic_address = await comatic.find_address_by_email(email)

        if comatic_address is None:
            address_body = _build_address(order)
            comatic_address = await comatic.create_address(address_body)

        address_id = comatic_address.Id
        if address_id is None:
            raise ComaticAPIError(f"Address creation failed for {order_id}")

        # ── Step 3: Precise Invoice ───────────────────────────────────────────
        detail_rows = await _build_invoice_rows(order, shopify)
        
        invoice_body = ComaticInvoiceCreate(
            DocumentDate=order.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            Status="Registered",
            AddressId=address_id,
            AddressForServiceId=address_id,
            InvoiceAddressId=address_id,
            CommissionRecipientAddressId=address_id,
            Terms=settings.comatic_default_sales_condition[:2],
            ChargeFactor=settings.comatic_default_charge_factor,
            VatType=settings.comatic_default_vat_type,
            CurrencyCode=order.currency[:3],
            AccountingRate=settings.comatic_default_accounting_rate,
            StockLocation=settings.comatic_default_stock_location,
            StatusOfStock=0,
            IsOpenPosition=not is_paid,
            ExternalId=str(order.id),
            Reference=order.name,
            BookingText=f"Shopify {order.name}",
            DetailRows=detail_rows,
        )
        
        invoice_data = await comatic.create_invoice(invoice_body)
        comatic_invoice_id = invoice_data.get("Id")
        doc_number = invoice_data.get("DocumentNumber")

        # ── Step 4: Payment ───────────────────────────────────────────────────
        if is_paid:
            payment_body = ComaticPaymentOnAccountCreate(
                AddressId=address_id,
                BookingText=f"Shopify {order.name}",
                Reference=order.name,
                Currency=order.currency[:3],
                Amount=order.total_price_float,
                BookingAmount=order.total_price_float,
                BookingDate=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                DocumentNumber=doc_number,
                VatCode=tax_mapper.get_vat_code(order.country_code, 0.0, "Payment"), # dummy vat for payment booking
            )
            await comatic.create_payment_on_account(address_id, payment_body)

        # ── Step 5: Mark Synced ───────────────────────────────────────────────
        await mark_order_synced(
            shopify_order_id=order_id,
            shopify_order_name=order.name,
            customer_email=email or "",
            financial_status=order.financial_status,
            payment_gateway=order.payment_gateway or "",
            total_price=order.total_price_float,
            currency=order.currency,
            comatic_invoice_id=comatic_invoice_id,
            comatic_address_id=address_id,
        )
        return True

    except Exception as exc:
        logger.exception("Order {name} FAILED: {err}", name=order.name, err=str(exc))
        await mark_order_failed(order_id, str(exc))
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Main sync entrypoint
# ─────────────────────────────────────────────────────────────────────────────

async def run_sync(since_hours: Optional[int] = None) -> dict:
    run_id = await start_sync_run()
    logger.info("=== Starting Accountant Precision Sync #{run_id} ===", run_id=run_id)

    stats = {"found": 0, "synced": 0, "skipped": 0, "failed": 0}

    try:
        async with ShopifyClient() as shopify:
            orders = await shopify.get_new_orders(since_hours=since_hours)
            stats["found"] = len(orders)

            async with ComaticClient() as comatic:
                for order in orders:
                    if await is_order_synced(str(order.id)):
                        stats["skipped"] += 1
                        continue

                    if await _process_order(order, shopify, comatic):
                        stats["synced"] += 1
                    else:
                        stats["failed"] += 1

    except Exception as exc:
        logger.exception("Sync aborted: {err}", err=str(exc))
        await finish_sync_run(run_id, stats["found"], stats["synced"], stats["skipped"], stats["failed"], "error")
        raise

    await finish_sync_run(run_id, stats["found"], stats["synced"], stats["skipped"], stats["failed"], "done")
    return stats
