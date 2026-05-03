import asyncio
import aiosqlite
from database import init_db
from config import settings

async def seed():
    print(f"Connecting to {settings.sqlite_db_path}...")
    await init_db()
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        # Dummy Order 1: Unpaid & Unfulfilled
        await db.execute("""
            INSERT OR REPLACE INTO orders (
                shopify_order_id, shopify_order_name, order_date, total_price, currency,
                financial_status, fulfillment_status, customer_email, shipping_country_code
            ) VALUES (?, ?, datetime('now'), ?, ?, ?, ?, ?, ?)
        """, ("dummy_1", "#DUMMY-UNPAID", 150.00, "CHF", "pending", "unfulfilled", "customer@example.com", "CH"))

        # Dummy Order 2: Paid & Unfulfilled
        await db.execute("""
            INSERT OR REPLACE INTO orders (
                shopify_order_id, shopify_order_name, order_date, total_price, currency,
                financial_status, fulfillment_status, customer_email, shipping_country_code
            ) VALUES (?, ?, datetime('now', '-1 day'), ?, ?, ?, ?, ?, ?)
        """, ("dummy_2", "#DUMMY-PAID", 89.90, "EUR", "PAID", "unfulfilled", "buyer@test.ch", "DE"))

        # Dummy Items
        await db.execute("""
            INSERT OR REPLACE INTO order_items (
                shopify_order_id, product_name, quantity, total_paid, collections
            ) VALUES (?, ?, ?, ?, ?)
        """, ("dummy_1", "Swiss Alpine Knife", 1, 150.00, '["Knives", "Swiss Made"]'))
        
        await db.commit()
    print("✅ Dummy data injected successfully!")

if __name__ == "__main__":
    asyncio.run(seed())
