"""
database.py — SQLite schema, migrations, and async query helpers via aiosqlite.
All functions are async and accept an optional `db` connection for transactional use.
"""
import aiosqlite
from datetime import datetime, timezone
from typing import Optional
from loguru import logger
from config import settings


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

CREATE_SYNCED_ORDERS = """
CREATE TABLE IF NOT EXISTS synced_orders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    shopify_order_id    TEXT    NOT NULL UNIQUE,
    shopify_order_name  TEXT,
    customer_email      TEXT,
    financial_status    TEXT,
    payment_gateway     TEXT,
    total_price         REAL,
    currency            TEXT,
    comatic_invoice_id  INTEGER,
    comatic_address_id  INTEGER,
    country_code        TEXT,
    vat_reason          TEXT,
    chf_amount          REAL,
    sync_status         TEXT    NOT NULL DEFAULT 'pending',
    error_message       TEXT,
    synced_at           TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_SYNC_RUNS = """
CREATE TABLE IF NOT EXISTS sync_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          TEXT    NOT NULL,
    finished_at         TEXT,
    orders_found        INTEGER DEFAULT 0,
    orders_synced       INTEGER DEFAULT 0,
    orders_skipped      INTEGER DEFAULT 0,
    orders_failed       INTEGER DEFAULT 0,
    status              TEXT    DEFAULT 'running'
);
"""


async def init_db() -> None:
    """Create tables if they don't exist and run migrations."""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(CREATE_SYNCED_ORDERS)
        await db.execute(CREATE_SYNC_RUNS)
        await db.commit()
    
    # Run column migrations for existing databases
    await migrate_db()
    logger.info("Database initialised and migrated at {path}", path=settings.sqlite_db_path)


async def migrate_db() -> None:
    """Safely adds missing columns to existing tables."""
    columns_to_add = {
        "synced_orders": [
            ("vat_reason", "TEXT"),
            ("chf_amount", "REAL"),
        ]
    }
    
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        for table, cols in columns_to_add.items():
            # Check existing columns
            async with db.execute(f"PRAGMA table_info({table})") as cursor:
                existing_cols = {row[1] for row in await cursor.fetchall()}
                
            for col_name, col_type in cols:
                if col_name not in existing_cols:
                    logger.info("Migrating DB: Adding column {c} to {t}", c=col_name, t=table)
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
        
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Order helpers
# ─────────────────────────────────────────────────────────────────────────────

async def is_order_synced(shopify_order_id: str) -> bool:
    """Return True if the order already has sync_status = 'synced'."""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        async with db.execute(
            "SELECT sync_status FROM synced_orders WHERE shopify_order_id = ?",
            (str(shopify_order_id),),
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None and row[0] == "synced"


async def mark_order_synced(
    shopify_order_id: str,
    shopify_order_name: str,
    customer_email: str,
    financial_status: str,
    payment_gateway: str,
    total_price: float,
    currency: str,
    comatic_invoice_id: Optional[int],
    comatic_address_id: Optional[int],
    country_code: str = "CH",
    vat_reason: Optional[str] = None,
    chf_amount: Optional[float] = None,
) -> None:
    """Upsert a successfully synced order record."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            """
            INSERT INTO synced_orders
                (shopify_order_id, shopify_order_name, customer_email, financial_status,
                 payment_gateway, total_price, currency, comatic_invoice_id,
                 comatic_address_id, country_code, vat_reason, chf_amount,
                 sync_status, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'synced', ?)
            ON CONFLICT(shopify_order_id) DO UPDATE SET
                sync_status         = 'synced',
                financial_status    = excluded.financial_status,
                payment_gateway     = excluded.payment_gateway,
                comatic_invoice_id  = excluded.comatic_invoice_id,
                comatic_address_id  = excluded.comatic_address_id,
                country_code        = excluded.country_code,
                vat_reason          = excluded.vat_reason,
                chf_amount          = excluded.chf_amount,
                error_message       = NULL,
                synced_at           = excluded.synced_at
            """,
            (
                str(shopify_order_id), shopify_order_name, customer_email,
                financial_status, payment_gateway, total_price, currency,
                comatic_invoice_id, comatic_address_id, country_code, 
                vat_reason, chf_amount, now,
            ),
        )
        await db.commit()
    logger.debug("Marked order {id} as synced in DB", id=shopify_order_id)


async def mark_order_failed(shopify_order_id: str, error_message: str) -> None:
    """
    Upsert a FAILED order. Critically, does NOT set sync_status='synced'
    so the cron job will retry it next run.
    """
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            """
            INSERT INTO synced_orders (shopify_order_id, sync_status, error_message)
            VALUES (?, 'failed', ?)
            ON CONFLICT(shopify_order_id) DO UPDATE SET
                sync_status   = 'failed',
                error_message = excluded.error_message
            """,
            (str(shopify_order_id), error_message),
        )
        await db.commit()
    logger.warning(
        "Marked order {id} as FAILED. Will retry tomorrow. Error: {err}",
        id=shopify_order_id,
        err=error_message,
    )


async def update_order_paid(shopify_order_id: str) -> None:
    """Update financial_status to 'paid' after Mark-as-Paid action."""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            "UPDATE synced_orders SET financial_status = 'paid' WHERE shopify_order_id = ?",
            (str(shopify_order_id),),
        )
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard queries
# ─────────────────────────────────────────────────────────────────────────────

async def get_recent_orders(days: int = 7) -> list[dict]:
    """Return orders synced in the last N days, most recent first."""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM synced_orders
            WHERE created_at >= datetime('now', ?)
            ORDER BY created_at DESC
            """,
            (f"-{days} days",),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_pending_orders() -> list[dict]:
    """Return orders that are synced but still unpaid (financial_status = 'pending')."""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM synced_orders
            WHERE sync_status = 'synced' AND financial_status != 'paid'
            ORDER BY created_at DESC
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_order_by_shopify_id(shopify_order_id: str) -> Optional[dict]:
    """Fetch a single order record by Shopify ID."""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM synced_orders WHERE shopify_order_id = ?",
            (str(shopify_order_id),),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Sync run audit log
# ─────────────────────────────────────────────────────────────────────────────

async def start_sync_run() -> int:
    """Insert a new sync run and return its ID."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        cursor = await db.execute(
            "INSERT INTO sync_runs (started_at) VALUES (?)", (now,)
        )
        await db.commit()
        return cursor.lastrowid


async def finish_sync_run(
    run_id: int,
    orders_found: int,
    orders_synced: int,
    orders_skipped: int,
    orders_failed: int,
    status: str = "done",
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            """
            UPDATE sync_runs SET
                finished_at     = ?,
                orders_found    = ?,
                orders_synced   = ?,
                orders_skipped  = ?,
                orders_failed   = ?,
                status          = ?
            WHERE id = ?
            """,
            (now, orders_found, orders_synced, orders_skipped, orders_failed, status, run_id),
        )
        await db.commit()
