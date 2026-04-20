"""
services/sync_engine.py — Orchestrates the full Shopify → Comatic sync pipeline.

Per-order flow:
  1. Skip if already synced (deduplication guard)
  2. Find or create Comatic address by email
  3. Map VAT codes (with CH fallback)
  4. Create Comatic CustomerInvoice
     - IsOpenPosition=True  → pending/bank-transfer orders
     - IsOpenPosition=False → already paid orders
  5. If paid: also post a PaymentOnAccount entry
  6. Mark as synced in SQLite; on any Comatic failure: mark as failed (retry tomorrow)
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
from models.shopify import ShopifyOrder
from services.comatic_client import ComaticAPIError, ComaticClient
from services.shopify_client import ShopifyClient


# ─────────────────────────────────────────────────────────────────────────────
# VAT code mapping
# ─────────────────────────────────────────────────────────────────────────────

# Swiss VAT codes (Comatic CH defaults – update once you run GET /masterdatas/vatcodes).
# Format: (country_code, bracket) → comatic_vat_code
# Typical CH codes are 3-char strings stored in Comatic master data.
_CH_RATE_MAP: dict[float, str] = {
    0.081: "NN",   # Standard rate 8.1%
    0.026: "HB",   # Reduced rate 2.6%
    0.038: "SB",   # Special rate 3.8%
    0.00:  "US",   # Exempt / zero-rated
}


def map_vat_code(country_code: str, tax_rate: float) -> str:
    """
    Map a (country_code, Shopify tax_rate) to a Comatic VatCode string.

    ┌─────────────────────────────────────────────────────────────────────────┐
    │ TODO: Replace the rate→code mapping below once you query                │
    │   GET /api/{v}/masterdatas/vatcodes                                     │
    │ on your live Comatic instance and note the real Code values.            │
    └─────────────────────────────────────────────────────────────────────────┘
    Falls back to settings.comatic_default_vat_code on any mismatch.
    """
    if country_code == "CH":
        # Find closest rate key (within 0.5% tolerance)
        for rate, code in _CH_RATE_MAP.items():
            if abs(tax_rate - rate) < 0.005:
                return code

    fallback = settings.comatic_default_vat_code
    logger.warning(
        "map_vat_code: no mapping for country={country} rate={rate:.3f}. "
        "Using fallback='{fallback}'. Update _CH_RATE_MAP or add your country.",
        country=country_code,
        rate=tax_rate,
        fallback=fallback,
    )
    return fallback


# ─────────────────────────────────────────────────────────────────────────────
# Per-order processing
# ─────────────────────────────────────────────────────────────────────────────

def _build_address(order: ShopifyOrder) -> ComaticAddressCreate:
    """Construct a Comatic address body from a Shopify order."""
    billing = order.billing_address
    currency = order.currency or "CHF"
    country_code = order.country_code

    # Derive language code from country (basic heuristic)
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


def _build_invoice(
    order: ShopifyOrder,
    address_id: int,
    is_paid: bool,
) -> ComaticInvoiceCreate:
    """Build the Comatic invoice body from a Shopify order."""
    doc_date = order.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    country_code = order.country_code

    detail_rows: list[ComaticInvoiceDetailRow] = []
    for item in order.line_items:
        tax_rate = item.effective_tax_rate
        vat_code = map_vat_code(country_code, tax_rate)
        detail_rows.append(
            ComaticInvoiceDetailRow(
                Description=item.title[:50] if item.title else "Article",
                Specification=item.sku,
                Quantity=float(item.quantity),
                SingleAmount=item.unit_price_float,
                TotalAmount=item.total_price_float,
                VatCode=vat_code,
                QuantityUnit="Stk",
                ExternalId=str(item.id),
            )
        )

    return ComaticInvoiceCreate(
        DocumentDate=doc_date,
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
        IsOpenPosition=not is_paid,              # True = still outstanding
        ExternalId=str(order.id)[:50],           # Shopify Order ID
        Reference=order.name[:50],               # e.g. "#1001"
        BookingText=f"Shopify {order.name}"[:50],
        DetailRows=detail_rows,
    )


async def _process_order(
    order: ShopifyOrder,
    comatic: ComaticClient,
) -> bool:
    """
    Process one Shopify order through the full sync pipeline.
    Returns True on success, False on failure.
    """
    order_id = str(order.id)
    logger.info(
        "Processing order {name} (id={id}) financial_status={status} gateway={gw}",
        name=order.name,
        id=order_id,
        status=order.financial_status,
        gw=order.payment_gateway,
    )

    email = order.customer_email
    is_paid = order.financial_status == "paid"

    try:
        # ── Step 1: Customer Management ──────────────────────────────────────
        comatic_address = None
        if email:
            comatic_address = await comatic.find_address_by_email(email)

        if comatic_address is None:
            logger.info(
                "Order {id}: no existing Comatic address found, creating one.", id=order_id
            )
            address_body = _build_address(order)
            comatic_address = await comatic.create_address(address_body)
        else:
            logger.info(
                "Order {id}: reusing Comatic address Id={addr_id}",
                id=order_id,
                addr_id=comatic_address.Id,
            )

        address_id = comatic_address.Id
        if address_id is None:
            raise ComaticAPIError(f"Comatic returned address with no Id for order {order_id}")

        # ── Step 2: Create Invoice ────────────────────────────────────────────
        invoice_body = _build_invoice(order, address_id, is_paid)
        invoice_data = await comatic.create_invoice(invoice_body)
        comatic_invoice_id = invoice_data.get("Id")
        doc_number = invoice_data.get("DocumentNumber")

        logger.info(
            "Order {id}: Comatic invoice created Id={inv} DocNum={doc} IsOpenPosition={open}",
            id=order_id,
            inv=comatic_invoice_id,
            doc=doc_number,
            open=not is_paid,
        )

        # ── Step 3: Payment (only for already-paid orders) ───────────────────
        if is_paid:
            payment_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            payment_body = ComaticPaymentOnAccountCreate(
                AddressId=address_id,
                BookingText=f"Shopify {order.name}",
                Reference=order.name,
                Currency=order.currency[:3],
                Amount=order.total_price_float,
                BookingAmount=order.total_price_float,
                BookingDate=payment_date,
                DocumentNumber=doc_number,
                VatCode=settings.comatic_default_vat_code,
            )
            await comatic.create_payment_on_account(address_id, payment_body)
            logger.info(
                "Order {id}: Comatic payment on account posted (is_paid=True).", id=order_id
            )
        else:
            logger.info(
                "Order {id}: left as open position (financial_status={status}, gateway={gw}). "
                "Requires manual confirmation.",
                id=order_id,
                status=order.financial_status,
                gw=order.payment_gateway,
            )

        # ── Step 4: Mark synced in SQLite ─────────────────────────────────────
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

    except ComaticAPIError as exc:
        logger.error(
            "Order {id} FAILED due to Comatic API error. Will retry tomorrow. Error: {err}",
            id=order_id,
            err=str(exc),
        )
        await mark_order_failed(order_id, str(exc))
        return False

    except Exception as exc:
        logger.exception(
            "Order {id} FAILED due to unexpected error: {err}", id=order_id, err=str(exc)
        )
        await mark_order_failed(order_id, str(exc))
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Main sync entrypoint
# ─────────────────────────────────────────────────────────────────────────────

async def run_sync(since_hours: Optional[int] = None) -> dict:
    """
    Main sync function. Fetches all new Shopify orders and syncs each to Comatic.
    Returns a stats dict for dashboard display.

    Called by:
      - sync_runner.py (cron)
      - web/app.py (Manual Sync button via FastAPI background task)
    """
    run_id = await start_sync_run()
    logger.info("=== Starting sync run #{run_id} ===", run_id=run_id)

    stats = {
        "found": 0,
        "synced": 0,
        "skipped": 0,
        "failed": 0,
    }

    try:
        async with ShopifyClient() as shopify:
            orders = await shopify.get_new_orders(since_hours=since_hours)
        stats["found"] = len(orders)

        async with ComaticClient() as comatic:
            for order in orders:
                order_id = str(order.id)
                if await is_order_synced(order_id):
                    logger.info("Order {id} already synced. Skipping.", id=order_id)
                    stats["skipped"] += 1
                    continue

                success = await _process_order(order, comatic)
                if success:
                    stats["synced"] += 1
                else:
                    stats["failed"] += 1

    except Exception as exc:
        logger.exception("Sync run #{run_id} aborted by unhandled exception: {err}", run_id=run_id, err=str(exc))
        await finish_sync_run(
            run_id,
            stats["found"], stats["synced"], stats["skipped"], stats["failed"],
            status="error",
        )
        raise

    await finish_sync_run(
        run_id,
        stats["found"], stats["synced"], stats["skipped"], stats["failed"],
        status="done",
    )
    logger.info(
        "=== Sync run #{run_id} complete: found={found} synced={synced} "
        "skipped={skipped} failed={failed} ===",
        run_id=run_id,
        **stats,
    )
    return stats
