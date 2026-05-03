"""
web/app.py — FastAPI dashboard for Shopify order tracking.
"""
import json
from collections import defaultdict
from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
from loguru import logger

from config import settings
from database import (
    init_db,
    get_recent_orders,
    update_order_paid,
    get_order_by_shopify_id,
    get_items_for_order,
)
from services.sync_engine import run_sync

app = FastAPI(title="Shopify Order Dashboard")
security = HTTPBasic()

def check_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if not settings.dashboard_password:
        return True
    
    correct_username = secrets.compare_digest(credentials.username, settings.dashboard_username)
    correct_password = secrets.compare_digest(credentials.password, settings.dashboard_password)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True


@app.on_event("startup")
async def startup_event():
    await init_db()


templates = Jinja2Templates(directory="web/templates")
app.mount("/static", StaticFiles(directory="web/static"), name="static")


def _revenue_by_currency(orders: list[dict]) -> dict[str, float]:
    """Sum total_price grouped by currency code."""
    totals: dict[str, float] = defaultdict(float)
    for o in orders:
        currency = (o.get("currency") or "?").upper()
        totals[currency] = round(totals[currency] + (o.get("total_price") or 0), 2)
    return dict(totals)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _ = Depends(check_auth)):
    try:
        orders = await get_recent_orders(days=365)

        # Load bank accounts from config
        try:
            bank_accounts = json.loads(settings.comatic_payment_accounts)
        except Exception:
            bank_accounts = {"Wise": "1001", "Bank Transfer": "1002"}

        unpaid      = [o for o in orders if (o.get("financial_status")  or "").upper() != "PAID"]
        unfulfilled = [o for o in orders if (o.get("fulfillment_status") or "").upper() not in ("FULFILLED", "RESTOCKED")]
        revenue     = _revenue_by_currency(orders)

        return templates.TemplateResponse("dashboard.html", {
            "request":      request,
            "orders":       orders,
            "unpaid":       unpaid,
            "unfulfilled":  unfulfilled,
            "bank_accounts": bank_accounts,
            "summary": {
                "total_orders":  len(orders),
                "unpaid_count":  len(unpaid),
                "unfulfilled_count": len(unfulfilled),
                "revenue":       revenue,
            },
            "msg":   request.query_params.get("msg"),
            "error": request.query_params.get("error"),
        })
    except Exception as e:
        logger.exception("Failed to render dashboard")
        return HTMLResponse(content=f"<pre>Error: {e}</pre>", status_code=500)


@app.get("/api/order-items/{shopify_order_id}")
async def get_order_items(shopify_order_id: str, _ = Depends(check_auth)):
    """Return line items for a given order as JSON (called by the modal)."""
    items = await get_items_for_order(shopify_order_id)
    return {"items": items}


@app.post("/sync")
async def trigger_sync():
    """Incremental sync — last N hours from .env."""
    try:
        await run_sync(since_hours=settings.sync_lookback_hours)
        return RedirectResponse(url="/?msg=Sync completed", status_code=303)
    except Exception as e:
        logger.error(f"Sync failed: {e}")
        return RedirectResponse(url=f"/?error=Sync failed: {str(e)}", status_code=303)


@app.post("/sync/full")
async def trigger_full_sync():
    """Full historical sync — last 365 days."""
    try:
        await run_sync(since_hours=365 * 24)
        return RedirectResponse(url="/?msg=Full sync completed", status_code=303)
    except Exception as e:
        logger.error(f"Full sync failed: {e}")
        return RedirectResponse(url=f"/?error=Full sync failed: {str(e)}", status_code=303)


@app.post("/mark-as-paid/{order_id}")
async def mark_as_paid(order_id: str, payment_method: str = Form(...)):
    """Mark an unpaid order as paid and record which account received it."""
    order = await get_order_by_shopify_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    try:
        await update_order_paid(order_id)
        logger.info(f"Order {order_id} marked as paid → account {payment_method}")
        return RedirectResponse(url="/?msg=Order marked as paid", status_code=303)
    except Exception as e:
        logger.error(f"Mark-as-paid failed: {e}")
        return RedirectResponse(url=f"/?error=Failed: {str(e)}", status_code=303)
