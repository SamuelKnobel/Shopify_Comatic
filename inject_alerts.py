import asyncio
import aiosqlite
from config import settings

async def inject_alerts():
    print(f"Injecting VAT alerts into {settings.sqlite_db_path}...")
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        # Order #1005: Mark as Reduced Rate
        await db.execute(
            "UPDATE synced_orders SET vat_reason = 'DE Reduced Rate (7%)' WHERE shopify_order_name = '#1005'"
        )
        # Order #1010: Mark as Export Case
        await db.execute(
            "UPDATE synced_orders SET vat_reason = 'Export Case (0%)' WHERE shopify_order_name = '#1010'"
        )
        await db.commit()
    print("Injection complete. 2 alerts created.")

if __name__ == "__main__":
    asyncio.run(inject_alerts())
