"""
web/app.py — FastAPI dashboard for the Shopify-Comatic accountant helper.
STABILIZED: Function name 'dashboard' and robust context passing.
"""
import json
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from config import settings
from database import (
    init_db,
    get_recent_orders, 
    get_pending_orders, 
    update_order_paid, 
    get_order_by_shopify_id
)
from services.sync_engine import run_sync
from services.comatic_client import ComaticClient
from services.shopify_client import ShopifyClient
from services.analytics import get_vat_liability_stats

app = FastAPI(title="Shopify-Comatic Premium Accountant Helper")

@app.on_event("startup")
async def startup_event():
    """Ensure DB is running and migrated."""
    await init_db()

# Templates & Static Files
templates = Jinja2Templates(directory="web/templates")
app.mount("/static", StaticFiles(directory="web/static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """
    Main Landing Page (Premium Dashboard):
    Displays Pending Reconciliation, VAT Analytics, and History.
    """
    try:
        pending = await get_pending_orders()
        recent = await get_recent_orders(days=7)
        vat_stats = await get_vat_liability_stats()
        
        # Load bank accounts from config
        try:
            bank_accounts_raw = settings.comatic_payment_accounts
            bank_accounts = json.loads(bank_accounts_raw)
        except Exception as e:
            logger.warning(f"Failed to parse payment accounts: {e}")
            bank_accounts = {"Wise Belgien": "1001", "Deutsche Reichsbank": "1002"}

        # Calculate high-level stats for the billboard
        total_recent = len(recent)
        pending_count = len(pending)
        success_rate = 0
        if total_recent > 0:
            synced_count = sum(1 for o in recent if o.get("sync_status") == "synced")
            success_rate = int((synced_count / total_recent) * 100)

        # Premium Summary Context
        context = {
            "request": request,
            "pending": pending or [],
            "recent": recent or [],
            "vat": vat_stats or {"by_country": [], "totals": {"tax": 0.0, "taxable": 0.0}, "kpis": {}},
            "bank_accounts": bank_accounts,
            "settings": settings,
            "summary": {
                "total": total_recent,
                "pending": pending_count,
                "success_rate": success_rate,
                "rev_eur": vat_stats["kpis"]["rev_24h_eur"],
                "rev_chf": vat_stats["kpis"]["rev_24h_chf"],
                "vat_alerts": vat_stats["kpis"]["vat_alerts"],
                "pending_chf": vat_stats["kpis"]["pending_chf"]
            }
        }
        
        return templates.TemplateResponse("dashboard.html", context)
    
    except Exception as e:
        logger.exception("Failed to render dashboard")
        return HTMLResponse(content=f"Critical Error: {str(e)}", status_code=500)

@app.get("/settings", response_class=HTMLResponse)
async def settings_view(request: Request):
    """Configuration overview page."""
    try:
        vat_mapping = json.loads(settings.comatic_vat_mapping)
        bank_accounts = json.loads(settings.comatic_payment_accounts)
    except:
        vat_mapping = {}
        bank_accounts = {}

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": settings,
            "vat_mapping": vat_mapping,
            "bank_accounts": bank_accounts,
        }
    )

@app.post("/sync")
async def trigger_sync():
    """Manual sync trigger."""
    try:
        await run_sync(since_hours=settings.sync_lookback_hours)
        return RedirectResponse(url="/?msg=Sync completed", status_code=303)
    except Exception as e:
        logger.error(f"Sync failed: {e}")
        return RedirectResponse(url="/?error=Sync failed", status_code=303)

@app.post("/mark-as-paid/{order_id}")
async def mark_as_paid(order_id: str, bank_account: str = Form(...)):
    """Reconcile a pending order."""
    order = await get_order_by_shopify_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    try:
        async with ComaticClient() as comatic:
            from models.comatic import ComaticPaymentOnAccountCreate
            from datetime import datetime, timezone
            payment = ComaticPaymentOnAccountCreate(
                AddressId=order["comatic_address_id"],
                Amount=order["total_price"],
                BookingAmount=order["total_price"],
                BookingDate=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                Currency=order["currency"],
                DocumentNumber="N/A",
                VatCode="NN",
                Account=bank_account,
                Reference=order["shopify_order_name"],
                BookingText=f"Manual: {order['shopify_order_name']}"
            )
            await comatic.create_payment_on_account(order["comatic_address_id"], payment)

        async with ShopifyClient() as shopify:
            await shopify.mark_order_as_paid(order_id)

        await update_order_paid(order_id)
        return RedirectResponse(url="/?msg=Order reconciled", status_code=303)
    except Exception as e:
        logger.error(f"Reconciliation failed: {e}")
        return RedirectResponse(url=f"/?error=Failed: {str(e)}", status_code=303)

@app.get("/logs/{order_name}")
async def get_order_logs(order_name: str):
    """
    Search the log file for entries matching the order name.
    Simple disk-based implementation for Accountant Tool.
    """
    import os
    if not os.path.exists(settings.log_file):
        return {"logs": ["Log file not found."]}
    
    matches = []
    try:
        with open(settings.log_file, "r") as f:
            # Get last 1000 lines to avoid memory issues
            lines = f.readlines()[-1000:]
            for line in lines:
                if order_name in line:
                    matches.append(line.strip())
    except Exception as e:
        return {"logs": [f"Error reading logs: {e}"]}
    
    return {"logs": matches or ["No matching log entries found for this order."]}
