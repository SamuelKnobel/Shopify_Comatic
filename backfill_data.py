import asyncio
import aiosqlite
import json
from config import settings

async def backfill_data():
    print(f"Opening database at {settings.sqlite_db_path}...")
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        
        # Get all synced orders
        async with db.execute("SELECT id, total_price, currency, vat_reason, chf_amount FROM synced_orders") as cursor:
            rows = await cursor.fetchall()
            print(f"Found {len(rows)} records to check.")
            
            for row in rows:
                updates = []
                params = []
                
                # Backfill CHF Amount if missing or 0
                if row["chf_amount"] is None or row["chf_amount"] == 0:
                    price = row["total_price"] or 0
                    curr = (row["currency"] or "EUR").upper()
                    
                    if curr == "CHF":
                        chf_val = price
                    else:
                        chf_val = round(price * settings.eur_chf_rate, 2)
                    
                    updates.append("chf_amount = ?")
                    params.append(chf_val)

                # Backfill VAT Reason if missing
                if row["vat_reason"] is None or row["vat_reason"] == "":
                    updates.append("vat_reason = ?")
                    params.append("Legacy Sync (Backfilled)")

                if updates:
                    sql = f"UPDATE synced_orders SET {', '.join(updates)} WHERE id = ?"
                    params.append(row["id"])
                    await db.execute(sql, tuple(params))
                    print(f"Updated Order ID {row['id']}")

        await db.commit()
    print("Backfill complete.")

if __name__ == "__main__":
    asyncio.run(backfill_data())
