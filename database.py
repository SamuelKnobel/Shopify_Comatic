"""
database.py — SQLite schema and async query helpers via aiosqlite.

Schema (v2):
  - orders       : One row per Shopify order (header data)
  - order_items  : One row per line item (product detail)
  - sync_runs    : Audit log of each sync execution
"""
import aiosqlite
from datetime import datetime, timezone
from typing import Optional
from loguru import logger
from config import settings


# ─────────────────────────────────────────────────────────────────────────────
# Schema DDL
# ─────────────────────────────────────────────────────────────────────────────

CREATE_ORDERS = """
CREATE TABLE IF NOT EXISTS orders (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Shopify identifiers
    shopify_order_id        TEXT    NOT NULL UNIQUE,
    shopify_order_name      TEXT,                        -- e.g. "#1001"
    shopify_customer_id     TEXT,

    -- Customer / shipping
    customer_email          TEXT,
    shipping_name           TEXT,
    shipping_address1       TEXT,
    shipping_address2       TEXT,
    shipping_city           TEXT,
    shipping_zip            TEXT,
    shipping_province       TEXT,
    shipping_country_code   TEXT,

    -- Financials
    currency                TEXT,                        -- payment currency
    total_price             REAL,
    shipping_cost           REAL,
    payment_gateway         TEXT,
    financial_status        TEXT,                        -- paid / pending / refunded …
    fulfillment_status      TEXT,                        -- fulfilled / unfulfilled / partial

    -- Dates
    order_date              TEXT,                        -- Shopify created_at (ISO 8601)
    shipped_date            TEXT,                        -- fulfillment created_at if available

    -- Sync metadata
    sync_status             TEXT    NOT NULL DEFAULT 'pending',
    error_message           TEXT,
    synced_at               TEXT,
    created_at              TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_ORDER_ITEMS = """
CREATE TABLE IF NOT EXISTS order_items (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Reference to parent order
    shopify_order_id        TEXT    NOT NULL REFERENCES orders(shopify_order_id),
    shopify_order_name      TEXT,

    -- Product identity
    shopify_line_item_id    TEXT,
    shopify_product_id      TEXT,
    product_name            TEXT,
    sku                     TEXT,
    product_type            TEXT,

    -- Pricing
    quantity                INTEGER,
    original_unit_price     REAL,                        -- list price per unit
    discounted_unit_price   REAL,                        -- actual price paid per unit
    total_discount          REAL,                        -- total discount on this line
    total_paid              REAL,                        -- discounted_unit_price * quantity

    -- Metadata
    collections             TEXT,                        -- JSON array of collection titles
    tags                    TEXT,                        -- JSON array of product tags
    payment_gateway         TEXT,                        -- inherited from order

    created_at              TEXT    NOT NULL DEFAULT (datetime('now'))
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


# ─────────────────────────────────────────────────────────────────────────────
# Initialisation
# ─────────────────────────────────────────────────────────────────────────────

async def init_db() -> None:
    """Create tables if they don't exist."""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(CREATE_ORDERS)
        await db.execute(CREATE_ORDER_ITEMS)
        await db.execute(CREATE_SYNC_RUNS)
        await db.commit()
    logger.info("Database initialised at {path}", path=settings.sqlite_db_path)


# ─────────────────────────────────────────────────────────────────────────────
# Order helpers
# ─────────────────────────────────────────────────────────────────────────────

async def is_order_synced(shopify_order_id: str) -> bool:
    """Return True if the order already has sync_status = 'synced'."""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        async with db.execute(
            "SELECT sync_status FROM orders WHERE shopify_order_id = ?",
            (str(shopify_order_id),),
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None and row[0] == "synced"


async def upsert_order(
    shopify_order_id: str,
    shopify_order_name: str,
    shopify_customer_id: Optional[str],
    customer_email: Optional[str],
    shipping_name: Optional[str],
    shipping_address1: Optional[str],
    shipping_address2: Optional[str],
    shipping_city: Optional[str],
    shipping_zip: Optional[str],
    shipping_province: Optional[str],
    shipping_country_code: Optional[str],
    currency: str,
    total_price: float,
    shipping_cost: float,
    payment_gateway: Optional[str],
    financial_status: str,
    fulfillment_status: Optional[str],
    order_date: str,
    shipped_date: Optional[str] = None,
) -> None:
    """Insert or update the orders table row for one Shopify order."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            """
            INSERT INTO orders (
                shopify_order_id, shopify_order_name, shopify_customer_id,
                customer_email,
                shipping_name, shipping_address1, shipping_address2,
                shipping_city, shipping_zip, shipping_province, shipping_country_code,
                currency, total_price, shipping_cost, payment_gateway,
                financial_status, fulfillment_status,
                order_date, shipped_date,
                sync_status, synced_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'synced',?)
            ON CONFLICT(shopify_order_id) DO UPDATE SET
                shopify_order_name      = excluded.shopify_order_name,
                shopify_customer_id     = excluded.shopify_customer_id,
                customer_email          = excluded.customer_email,
                shipping_name           = excluded.shipping_name,
                shipping_address1       = excluded.shipping_address1,
                shipping_address2       = excluded.shipping_address2,
                shipping_city           = excluded.shipping_city,
                shipping_zip            = excluded.shipping_zip,
                shipping_province       = excluded.shipping_province,
                shipping_country_code   = excluded.shipping_country_code,
                currency                = excluded.currency,
                total_price             = excluded.total_price,
                shipping_cost           = excluded.shipping_cost,
                payment_gateway         = excluded.payment_gateway,
                financial_status        = excluded.financial_status,
                fulfillment_status      = excluded.fulfillment_status,
                order_date              = excluded.order_date,
                shipped_date            = excluded.shipped_date,
                sync_status             = 'synced',
                error_message           = NULL,
                synced_at               = excluded.synced_at
            """,
            (
                str(shopify_order_id), shopify_order_name, shopify_customer_id,
                customer_email,
                shipping_name, shipping_address1, shipping_address2,
                shipping_city, shipping_zip, shipping_province, shipping_country_code,
                currency, total_price, shipping_cost, payment_gateway,
                financial_status, fulfillment_status,
                order_date, shipped_date,
                now,
            ),
        )
        await db.commit()
    logger.debug("Upserted order {name} in DB", name=shopify_order_name)


async def upsert_order_items(shopify_order_id: str, items: list[dict]) -> None:
    """
    Replace all line items for an order.
    Each dict in `items` should contain the order_items columns.
    """
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        # Delete existing items for this order first (clean upsert)
        await db.execute(
            "DELETE FROM order_items WHERE shopify_order_id = ?",
            (str(shopify_order_id),),
        )
        for item in items:
            await db.execute(
                """
                INSERT INTO order_items (
                    shopify_order_id, shopify_order_name,
                    shopify_line_item_id, shopify_product_id,
                    product_name, sku, product_type,
                    quantity,
                    original_unit_price, discounted_unit_price,
                    total_discount, total_paid,
                    collections, tags,
                    payment_gateway, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(shopify_order_id),
                    item.get("shopify_order_name"),
                    item.get("shopify_line_item_id"),
                    item.get("shopify_product_id"),
                    item.get("product_name"),
                    item.get("sku"),
                    item.get("product_type"),
                    item.get("quantity"),
                    item.get("original_unit_price"),
                    item.get("discounted_unit_price"),
                    item.get("total_discount"),
                    item.get("total_paid"),
                    item.get("collections"),  # stored as JSON string
                    item.get("tags"),          # stored as JSON string
                    item.get("payment_gateway"),
                    now,
                ),
            )
        await db.commit()
    logger.debug(
        "Upserted {n} items for order {id}", n=len(items), id=shopify_order_id
    )


async def mark_order_failed(shopify_order_id: str, error_message: str) -> None:
    """Mark an order as failed so the next sync run will retry it."""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            """
            INSERT INTO orders (shopify_order_id, sync_status, error_message)
            VALUES (?, 'failed', ?)
            ON CONFLICT(shopify_order_id) DO UPDATE SET
                sync_status   = 'failed',
                error_message = excluded.error_message
            """,
            (str(shopify_order_id), error_message),
        )
        await db.commit()
    logger.warning(
        "Order {id} marked FAILED: {err}", id=shopify_order_id, err=error_message
    )


async def update_order_paid(shopify_order_id: str) -> None:
    """Update financial_status to 'paid' after a manual Mark-as-Paid action."""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            "UPDATE orders SET financial_status = 'PAID' WHERE shopify_order_id = ?",
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
            SELECT * FROM orders
            WHERE created_at >= datetime('now', ?)
            ORDER BY order_date DESC
            """,
            (f"-{days} days",),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_pending_orders() -> list[dict]:
    """Return orders that are synced but still unpaid."""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM orders
            WHERE sync_status = 'synced' AND UPPER(financial_status) != 'PAID'
            ORDER BY order_date DESC
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_order_by_shopify_id(shopify_order_id: str) -> Optional[dict]:
    """Fetch a single order record by Shopify ID."""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM orders WHERE shopify_order_id = ?",
            (str(shopify_order_id),),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_items_for_order(shopify_order_id: str) -> list[dict]:
    """Return all line items for a given order."""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM order_items WHERE shopify_order_id = ? ORDER BY id",
            (str(shopify_order_id),),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


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
