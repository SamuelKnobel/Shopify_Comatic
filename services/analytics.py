"""
services/analytics.py — Logic to aggregate sync data for accountant insights.
"""
import aiosqlite
from config import settings

async def get_vat_liability_stats() -> dict:
    """
    Returns a breakdown of VAT Liability grouped by Country and Sync Status.
    Format:
    {
        "by_country": [
            {"country": "CH", "taxable_amount": 1000.0, "tax_amount": 81.0, "rate_cat": "Standard"},
            ...
        ],
        "totals": {"taxable": 5000.0, "tax": 400.0}
    }
    """
    stats = {
        "by_country": [],
        "totals": {"taxable": 0.0, "tax": 0.0}
    }

    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        
        # In a real app, we'd calculate this from line items. 
        # For this dashboard helper, we'll aggregate by country from the synced_orders table
        # and provide a reasonable estimation based on the prices and known rates.
        
        query = """
            SELECT 
                country_code,
                SUM(total_price) as total_taxable,
                currency
            FROM synced_orders
            WHERE sync_status = 'synced'
            GROUP BY country_code, currency
        """
        
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                country = row["country_code"] or "CH"
                taxable = row["total_taxable"] or 0.0
                
                # Heuristic estimation for the dashboard (Accountant Tool)
                # In production, we should store line-level VAT in a separate table for perfect reporting.
                # For now, we'll use the common standard rates for the breakdown.
                rate = 0.081 if country == "CH" else 0.19 # Dummy logic for the chart
                tax = taxable * (rate / (1 + rate)) # Extract tax from gross
                
                stats["by_country"].append({
                    "country": country,
                    "taxable_amount": round(taxable - tax, 2),
                    "tax_amount": round(tax, 2),
                    "currency": row["currency"]
                })
                stats["totals"]["taxable"] += taxable - tax
                stats["totals"]["tax"] += tax

    stats["totals"]["taxable"] = round(stats["totals"]["taxable"], 2)
    stats["totals"]["tax"] = round(stats["totals"]["tax"], 2)
    
    return stats
