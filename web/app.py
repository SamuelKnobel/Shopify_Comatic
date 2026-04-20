"""
web/app.py — FastAPI dashboard for the Shopify-Comatic bridge.

Routes:
  GET  /              → dashboard.html     (last 7 days of orders)
  GET  /pending       → pending.html       (unpaid orders requiring action)
  POST /sync          → triggers manual sync run (background task) → redirect /
  POST /orders/{id}/mark-paid → dual action: Comatic + Shopify payment → JSON
"""
import asyncio
import sys
import os

# Allow imports from the project root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from datetime import datetime, timezone
from loguru import logger

from database import (
    init_db,
    get_recent_orders,
    get_pending_orders,
    get_order_by_shopify_id,
    update_order_paid,
)
from logging_setup import setup_logging
from services.sync_engine import run_sync
from services.comatic_client import ComaticClient, ComaticAPIError
from services.shopify_client import ShopifyClient, ShopifyAPIError
from models.comatic import ComaticPaymentOnAccountCreate
from config import settings

# ── App bootstrap ────────────────────────────────────────────────────────────

setup_logging()

app = FastAPI(
    title="Shopify ↔ Comatic Bridge",
    description="Sync dashboard for B2C order synchronisation",
    version="1.0.0",
)

# Resolve absolute paths relative to this file
_here = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(_here, "templates"))
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(_here, "static")),
    name="static",
)

# Track running sync status to show spinner on dashboard
_sync_running: bool = False
_last_sync_stats: dict = {}


@app.on_event("startup")
async def startup_event() -> None:
    await init_db()
    logger.info("FastAPI app started. Database ready.")


# ── Helper ───────────────────────────────────────────────────────────────────

def _fmt_orders(orders: list[dict]) -> list[dict]:
    """Add display-friendly fields to order dicts."""
    for o in orders:
        o["is_paid"] = o.get("financial_status") == "paid"
        o["status_badge"] = {
            "synced":  ("bg-emerald", "✓ Synced"),
            "failed":  ("bg-red",     "✗ Failed"),
            "pending": ("bg-amber",   "⏳ Pending"),
        }.get(o.get("sync_status", ""), ("bg-gray", o.get("sync_status", "?")))
    return orders


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, name="dashboard")
async def dashboard(request: Request) -> HTMLResponse:
    """Main dashboard — last 7 days of sync activity."""
    orders = _fmt_orders(await get_recent_orders(days=7))
    pending_count = len([o for o in orders if o.get("financial_status") != "paid" and o.get("sync_status") == "synced"])
    failed_count  = len([o for o in orders if o.get("sync_status") == "failed"])
    paid_count    = len([o for o in orders if o.get("financial_status") == "paid"])
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "orders": orders,
            "pending_count": pending_count,
            "failed_count": failed_count,
            "paid_count": paid_count,
            "total_count": len(orders),
            "sync_running": _sync_running,
            "last_sync_stats": _last_sync_stats,
            "now": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        },
    )


@app.get("/pending", response_class=HTMLResponse, name="pending")
async def pending(request: Request) -> HTMLResponse:
    """Pending orders view — unpaid orders that may require manual action."""
    orders = _fmt_orders(await get_pending_orders())
    return templates.TemplateResponse(
        "pending.html",
        {
            "request": request,
            "orders": orders,
            "count": len(orders),
            "now": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        },
    )


@app.post("/sync", name="manual_sync")
async def manual_sync(background_tasks: BackgroundTasks) -> RedirectResponse:
    """
    Trigger a manual sync run in the background.
    Returns immediately; sync runs asynchronously.
    """
    global _sync_running, _last_sync_stats
    if _sync_running:
        logger.info("Manual sync requested but sync already running.")
        return RedirectResponse("/", status_code=303)

    async def _run_sync_task() -> None:
        global _sync_running, _last_sync_stats
        _sync_running = True
        logger.info("Manual sync started via dashboard.")
        try:
            stats = await run_sync()
            _last_sync_stats = stats
            logger.info("Manual sync completed. stats={stats}", stats=stats)
        except Exception:
            logger.exception("Manual sync failed with unhandled exception.")
        finally:
            _sync_running = False

    background_tasks.add_task(_run_sync_task)
    return RedirectResponse("/", status_code=303)


@app.post("/orders/{shopify_id}/mark-paid", name="mark_paid")
async def mark_paid(shopify_id: str, request: Request) -> JSONResponse:
    """
    Dual-action "Mark as Paid":
      1. POST payment on account to Comatic
      2. POST payment transaction to Shopify
      3. Update local DB financial_status → 'paid'

    If either API call fails, rolls back (no DB update) and returns 500.
    """
    logger.info("Mark-as-Paid requested for Shopify order {id}", id=shopify_id)

    order_record = await get_order_by_shopify_id(shopify_id)
    if not order_record:
        raise HTTPException(status_code=404, detail=f"Order {shopify_id} not found in local DB")

    if order_record.get("financial_status") == "paid":
        return JSONResponse({"status": "already_paid", "message": "Order is already marked as paid."})

    comatic_address_id = order_record.get("comatic_address_id")
    comatic_invoice_id = order_record.get("comatic_invoice_id")
    amount = order_record.get("total_price", 0.0)
    currency = order_record.get("currency", "CHF")
    order_name = order_record.get("shopify_order_name", shopify_id)

    if not comatic_address_id:
        raise HTTPException(
            status_code=422,
            detail="No Comatic address ID recorded for this order. Cannot proceed.",
        )

    try:
        # ── Step 1: Comatic payment ───────────────────────────────────────────
        payment_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        payment_body = ComaticPaymentOnAccountCreate(
            AddressId=comatic_address_id,
            BookingText=f"Shopify {order_name}",
            Reference=order_name,
            Currency=currency[:3],
            Amount=amount,
            BookingAmount=amount,
            BookingDate=payment_date,
            VatCode=settings.comatic_default_vat_code,
        )
        async with ComaticClient() as comatic:
            await comatic.create_payment_on_account(comatic_address_id, payment_body)
        logger.info("Comatic payment posted for order {id}", id=shopify_id)

        # ── Step 2: Shopify payment ───────────────────────────────────────────
        async with ShopifyClient() as shopify:
            await shopify.mark_order_paid(int(shopify_id), amount, currency)
        logger.info("Shopify order {id} marked as paid.", id=shopify_id)

        # ── Step 3: Update local DB ───────────────────────────────────────────
        await update_order_paid(shopify_id)
        logger.info("Local DB updated: order {id} financial_status → 'paid'", id=shopify_id)

        return JSONResponse(
            {"status": "success", "message": f"Order {order_name} marked as paid in both Comatic and Shopify."}
        )

    except (ComaticAPIError, ShopifyAPIError) as exc:
        logger.error(
            "Mark-as-Paid FAILED for order {id}: {err}", id=shopify_id, err=str(exc)
        )
        raise HTTPException(status_code=502, detail=f"API error: {exc}")

    except Exception as exc:
        logger.exception("Unexpected error in mark-paid for order {id}", id=shopify_id)
        raise HTTPException(status_code=500, detail=str(exc))
