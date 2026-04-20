"""
services/analytics.py — Logic to aggregate sync data for accountant insights.
"""
import aiosqlite
from config import settings

async def get_vat_liability_stats() -> dict:
    """
    Returns a breakdown of VAT Liability and core Fintech KPIs (24h Revenue, VAT Alerts).
    """
    stats = {
        "by_country": [],
        "totals": {"taxable": 0.0, "tax": 0.0},
        "kpis": {
            "rev_24h_eur": 0.0,
            "rev_24h_chf": 0.0,
            "vat_alerts": 0,
            "pending_chf": 0.0
        }
    }

    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        
        # 1. Main VAT Breakdown (Synced Orders Only)
        query_vat = """
            SELECT country_code, currency, SUM(total_price) as total_taxable
            FROM synced_orders
            WHERE sync_status = 'synced'
            GROUP BY country_code, currency
        """
        async with db.execute(query_vat) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                country = row["country_code"] or "CH"
                taxable = row["total_taxable"] or 0.0
                rate = 0.081 if country == "CH" else 0.19 
                tax = taxable * (rate / (1 + rate))
                stats["by_country"].append({
                    "country": country,
                    "taxable_amount": round(taxable - tax, 2),
                    "tax_amount": round(tax, 2),
                })
                stats["totals"]["taxable"] += taxable - tax
                stats["totals"]["tax"] += tax

        # 2. KPI Pulse (Last 24h & Alerts)
        query_kpis = """
            SELECT 
                SUM(total_price) as total_eur,
                SUM(chf_amount) as total_chf,
                COUNT(*) FILTER (WHERE vat_reason LIKE '%Reduced%' OR vat_reason LIKE '%Export%') as alerts,
                SUM(chf_amount) FILTER (WHERE financial_status != 'paid') as pending_chf
            FROM synced_orders
            WHERE sync_status = 'synced' AND created_at >= datetime('now', '-1 day')
        """
        async with db.execute(query_kpis) as cursor:
            row = await cursor.fetchone()
            if row:
                stats["kpis"]["rev_24h_eur"] = round(row["total_eur"] or 0.0, 2)
                stats["kpis"]["rev_24h_chf"] = round(row["total_chf"] or 0.0, 2)
                stats["kpis"]["vat_alerts"] = row["alerts"] or 0
                stats["kpis"]["pending_chf"] = round(row["pending_chf"] or 0.0, 2)

    stats["totals"]["taxable"] = round(stats["totals"]["taxable"], 2)
    stats["totals"]["tax"] = round(stats["totals"]["tax"], 2)
    return stats
