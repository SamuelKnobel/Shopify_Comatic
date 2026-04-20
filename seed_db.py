"""
seed_db.py — Populates the local SQLite database with dummy data for testing the dashboard.
"""
import asyncio
import aiosqlite
from datetime import datetime, timedelta, timezone
from config import settings
from database import init_db

async def seed():
    print(f"Seeding database at {settings.sqlite_db_path}...")
    await init_db()

    dummy_orders = [
        # 1. Successfully synced and already PAID (Stripe)
        {
            "shopify_order_id": "10001",
            "shopify_order_name": "#1001",
            "customer_email": "john.doe@example.com",
            "financial_status": "paid",
            "payment_gateway": "stripe",
            "total_price": 125.50,
            "currency": "CHF",
            "comatic_invoice_id": 5001,
            "comatic_address_id": 401,
            "sync_status": "synced",
            "error_message": None,
            "synced_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        },
        # 2. Successfully synced but PENDING (Bank Deposit) -> This one can be "Marked as Paid"
        {
            "shopify_order_id": "10002",
            "shopify_order_name": "#1002",
            "customer_email": "jane.smith@example.ch",
            "financial_status": "pending",
            "payment_gateway": "manual",
            "total_price": 450.00,
            "currency": "CHF",
            "comatic_invoice_id": 5002,
            "comatic_address_id": 402,
            "sync_status": "synced",
            "error_message": None,
            "synced_at": (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),
        },
        # 3. Failed sync (Simulated error)
        {
            "shopify_order_id": "10003",
            "shopify_order_name": "#1003",
            "customer_email": "bob@globex.corp",
            "financial_status": "paid",
            "payment_gateway": "shopify_payments",
            "total_price": 89.95,
            "currency": "CHF",
            "comatic_invoice_id": None,
            "comatic_address_id": None,
            "sync_status": "failed",
            "error_message": "Comatic API returned 500 Internal Server Error: 'Constraint violation'",
            "synced_at": None,
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat(),
        },
        # 4. Another Pending Order
        {
            "shopify_order_id": "10004",
            "shopify_order_name": "#1004",
            "customer_email": "alice.w@outlook.com",
            "financial_status": "pending",
            "payment_gateway": "manual",
            "total_price": 12.00,
            "currency": "CHF",
            "comatic_invoice_id": 5004,
            "comatic_address_id": 404,
            "sync_status": "synced",
            "error_message": None,
            "synced_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            "created_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        }
    ]

    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        for order in dummy_orders:
            await db.execute(
                """
                INSERT OR REPLACE INTO synced_orders 
                (shopify_order_id, shopify_order_name, customer_email, financial_status, 
                 payment_gateway, total_price, currency, comatic_invoice_id, 
                 comatic_address_id, sync_status, error_message, synced_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order["shopify_order_id"],
                    order["shopify_order_name"],
                    order["customer_email"],
                    order["financial_status"],
                    order["payment_gateway"],
                    order["total_price"],
                    order["currency"],
                    order["comatic_invoice_id"],
                    order["comatic_address_id"],
                    order["sync_status"],
                    order["error_message"],
                    order["synced_at"],
                    order["created_at"],
                )
            )
        await db.commit()

    print("DONE: Database seeded with 4 dummy orders.")

if __name__ == "__main__":
    asyncio.run(seed())
