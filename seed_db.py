"""
seed_db.py — Populates the local SQLite database with meaningful dummy data for the Accountant Dashboard.
"""
import asyncio
import aiosqlite
import os
from datetime import datetime, timedelta, timezone
from config import settings
from database import init_db

async def seed():
    if os.path.exists(settings.sqlite_db_path):
        os.remove(settings.sqlite_db_path)
    await init_db()

    dummy_orders = [
        # Country: CH
        {"shopify_order_id": "1001", "name": "#1001", "email": "a@ch.com", "country": "CH", "status": "paid", "total": 1500.0, "cur": "CHF", "sync": "synced"},
        {"shopify_order_id": "1002", "name": "#1002", "email": "b@ch.com", "country": "CH", "status": "pending", "total": 240.0, "cur": "CHF", "sync": "synced"},
        {"shopify_order_id": "1003", "name": "#1003", "email": "c@ch.com", "country": "CH", "status": "pending", "total": 85.0, "cur": "CHF", "sync": "synced"},
        
        # Country: DE
        {"shopify_order_id": "1004", "name": "#1004", "email": "d@de.com", "country": "DE", "status": "paid", "total": 5600.0, "cur": "EUR", "sync": "synced"},
        {"shopify_order_id": "1005", "name": "#1005", "email": "e@de.com", "country": "DE", "status": "pending", "total": 1200.0, "cur": "EUR", "sync": "synced"},
        
        # Country: FR
        {"shopify_order_id": "1006", "name": "#1006", "email": "f@fr.com", "country": "FR", "status": "paid", "total": 3400.0, "cur": "EUR", "sync": "synced"},
        {"shopify_order_id": "1007", "name": "#1007", "email": "g@fr.com", "country": "FR", "status": "pending", "total": 450.0, "cur": "EUR", "sync": "synced"},
        
        # Country: IT
        {"shopify_order_id": "1008", "name": "#1008", "email": "h@it.com", "country": "IT", "status": "paid", "total": 2100.0, "cur": "EUR", "sync": "synced"},
        
        # Country: AT
        {"shopify_order_id": "1009", "name": "#1009", "email": "i@at.com", "country": "AT", "status": "paid", "total": 980.0, "cur": "EUR", "sync": "synced"},
        
        # Failed Syncs
        {"shopify_order_id": "1010", "name": "#1010", "email": "j@fail.com", "country": "DE", "status": "paid", "total": 120.0, "cur": "EUR", "sync": "failed", "err": "Comatic API timeout"},
        {"shopify_order_id": "1011", "name": "#1011", "email": "k@fail.com", "country": "CH", "status": "paid", "total": 55.0, "cur": "CHF", "sync": "failed", "err": "Invalid address format"},
    ]

    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        for o in dummy_orders:
            now = (datetime.now(timezone.utc) - timedelta(days=o.get("id_num", 0) % 5)).isoformat()
            await db.execute(
                """
                INSERT INTO synced_orders 
                (shopify_order_id, shopify_order_name, customer_email, country_code, financial_status, 
                 payment_gateway, total_price, currency, comatic_invoice_id, 
                 comatic_address_id, sync_status, error_message, synced_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    o["shopify_order_id"], o["name"], o["email"], o["country"], o["status"],
                    "stripe" if o["status"] == "paid" else "manual", o["total"], o["cur"],
                    5000 + int(o["shopify_order_id"]), 4000 + int(o["shopify_order_id"]),
                    o["sync"], o.get("err"), now if o["sync"] == "synced" else None, now
                )
            )
        await db.commit()

    print(f"DONE: Database seeded with {len(dummy_orders)} diverse accountant-ready orders.")

if __name__ == "__main__":
    asyncio.run(seed())
