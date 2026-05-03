"""
services/sync_engine.py — Shopify → SQLite sync pipeline (v2).

Strategy:
  1. REST API  : paginated poll for new order IDs since last lookback window
  2. GraphQL   : one query per order fetches full detail
                 (line items, shipping, discounts, product collections/tags)
  3. SQLite    : upsert into `orders` + `order_items` tables
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from config import settings
from database import (
    finish_sync_run,
    is_order_synced,
    mark_order_failed,
    start_sync_run,
    upsert_order,
    upsert_order_items,
)
from services.shopify_client import ShopifyAPIError, ShopifyClient

# ─────────────────────────────────────────────────────────────────────────────
# GraphQL query — full order detail
# ─────────────────────────────────────────────────────────────────────────────

ORDER_DETAIL_QUERY = """
query($id: ID!) {
  order(id: $id) {
    name
    createdAt
    displayFinancialStatus
    displayFulfillmentStatus
    currencyCode
    paymentGatewayNames
    totalPriceSet       { shopMoney { amount currencyCode } }
    totalShippingPriceSet { shopMoney { amount currencyCode } }
    customer {
      id
      email
    }
    shippingAddress {
      name
      address1
      address2
      city
      zip
      provinceCode
      countryCodeV2
    }
    fulfillments(first: 1) {
      createdAt
    }
    lineItems(first: 100) {
      edges {
        node {
          id
          title
          quantity
          sku
          product {
            id
            productType
            tags
            collections(first: 10) {
              edges { node { title } }
            }
          }
          originalUnitPriceSet {
            shopMoney { amount currencyCode }
          }
          discountedUnitPriceSet {
            shopMoney { amount currencyCode }
          }
          discountAllocations {
            allocatedAmountSet {
              shopMoney { amount currencyCode }
            }
          }
        }
      }
    }
  }
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Transform GraphQL response → DB rows
# ─────────────────────────────────────────────────────────────────────────────

def _parse_order(gql_order: dict, shopify_order_id: str) -> dict:
    """Extract flat order header fields from a GraphQL order object."""
    shipping = gql_order.get("shippingAddress") or {}
    customer = gql_order.get("customer") or {}
    fulfillments = gql_order.get("fulfillments") or []
    
    total_price_raw = (
        gql_order.get("totalPriceSet", {}).get("shopMoney", {}).get("amount", 0)
    )
    shipping_cost_raw = (
        gql_order.get("totalShippingPriceSet", {}).get("shopMoney", {}).get("amount", 0)
    )
    gateways = gql_order.get("paymentGatewayNames") or []

    return {
        "shopify_order_id":       shopify_order_id,
        "shopify_order_name":     gql_order.get("name"),
        "shopify_customer_id":    customer.get("id"),
        "customer_email":         customer.get("email"),
        "shipping_name":          shipping.get("name"),
        "shipping_address1":      shipping.get("address1"),
        "shipping_address2":      shipping.get("address2"),
        "shipping_city":          shipping.get("city"),
        "shipping_zip":           shipping.get("zip"),
        "shipping_province":      shipping.get("provinceCode"),
        "shipping_country_code":  shipping.get("countryCodeV2"),
        "currency":               gql_order.get("currencyCode"),
        "total_price":            float(total_price_raw),
        "shipping_cost":          float(shipping_cost_raw),
        "payment_gateway":        gateways[0] if gateways else None,
        "financial_status":       gql_order.get("displayFinancialStatus"),
        "fulfillment_status":     gql_order.get("displayFulfillmentStatus"),
        "order_date":             gql_order.get("createdAt"),
        "shipped_date":           fulfillments[0].get("createdAt") if fulfillments else None,
    }


def _parse_line_items(gql_order: dict, shopify_order_id: str, payment_gateway: Optional[str]) -> list[dict]:
    """Build the list of order_items rows from a GraphQL order object."""
    items = []
    order_name = gql_order.get("name")
    
    for edge in gql_order.get("lineItems", {}).get("edges", []):
        node = edge["node"]
        product = node.get("product") or {}
        
        original_unit = float(
            node.get("originalUnitPriceSet", {}).get("shopMoney", {}).get("amount", 0)
        )
        discounted_unit = float(
            node.get("discountedUnitPriceSet", {}).get("shopMoney", {}).get("amount", 0)
        )
        qty = node.get("quantity", 1)
        
        total_discount = sum(
            float(da.get("allocatedAmountSet", {}).get("shopMoney", {}).get("amount", 0))
            for da in (node.get("discountAllocations") or [])
        )
        total_paid = round(discounted_unit * qty, 2)
        
        # Collections and tags as JSON strings for storage
        collections = [
            e["node"]["title"]
            for e in (product.get("collections") or {}).get("edges", [])
        ]
        tags = product.get("tags") or []
        
        # Strip gid:// prefix for clean storage
        line_item_gid = node.get("id", "")
        product_gid   = product.get("id", "")
        line_item_id  = line_item_gid.split("/")[-1] if line_item_gid else None
        product_id    = product_gid.split("/")[-1]   if product_gid   else None
        
        items.append({
            "shopify_order_id":      shopify_order_id,
            "shopify_order_name":    order_name,
            "shopify_line_item_id":  line_item_id,
            "shopify_product_id":    product_id,
            "product_name":          node.get("title"),
            "sku":                   node.get("sku"),
            "product_type":          product.get("productType"),
            "quantity":              qty,
            "original_unit_price":   original_unit,
            "discounted_unit_price": discounted_unit,
            "total_discount":        round(total_discount, 2),
            "total_paid":            total_paid,
            "collections":           json.dumps(collections),
            "tags":                  json.dumps(tags),
            "payment_gateway":       payment_gateway,
        })
    
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Per-order sync
# ─────────────────────────────────────────────────────────────────────────────

async def _sync_one_order(order_id: str, shopify: ShopifyClient) -> bool:
    """Fetch full order detail via GraphQL and write to SQLite. Returns True on success."""
    gid = f"gid://shopify/Order/{order_id}"
    try:
        result = await shopify.graphql(ORDER_DETAIL_QUERY, {"id": gid})
        gql_order = result.get("order")
        
        if not gql_order:
            raise ValueError(f"GraphQL returned no order data for {gid}")
        
        order_row = _parse_order(gql_order, order_id)
        item_rows = _parse_line_items(gql_order, order_id, order_row.get("payment_gateway"))
        
        await upsert_order(**order_row)
        await upsert_order_items(order_id, item_rows)
        
        logger.info(
            "✅ Synced order {name} → {n} items",
            name=order_row.get("shopify_order_name"),
            n=len(item_rows),
        )
        return True
    
    except Exception as exc:
        logger.exception("Order {id} FAILED: {err}", id=order_id, err=str(exc))
        await mark_order_failed(order_id, str(exc))
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Main sync entrypoint
# ─────────────────────────────────────────────────────────────────────────────

async def run_sync(since_hours: Optional[int] = None) -> dict:
    """
    Full sync pipeline:
      1. REST → get list of order IDs created in the lookback window
      2. GraphQL → fetch rich detail per order
      3. SQLite → upsert orders + order_items
    """
    run_id = await start_sync_run()
    logger.info("=== Starting Shopify → SQLite Sync #{run_id} ===", run_id=run_id)

    stats = {"found": 0, "synced": 0, "skipped": 0, "failed": 0}

    try:
        async with ShopifyClient() as shopify:
            orders = await shopify.get_new_orders(since_hours=since_hours)
            stats["found"] = len(orders)
            logger.info("Found {n} orders in lookback window.", n=stats["found"])

            for order in orders:
                order_id = str(order.id)
                if await is_order_synced(order_id):
                    stats["skipped"] += 1
                    continue

                if await _sync_one_order(order_id, shopify):
                    stats["synced"] += 1
                else:
                    stats["failed"] += 1

    except Exception as exc:
        logger.exception("Sync aborted: {err}", err=str(exc))
        await finish_sync_run(
            run_id, stats["found"], stats["synced"],
            stats["skipped"], stats["failed"], "error"
        )
        raise

    await finish_sync_run(
        run_id, stats["found"], stats["synced"],
        stats["skipped"], stats["failed"], "done"
    )
    logger.info(
        "=== Sync #{run_id} done: found={found} synced={synced} skipped={skipped} failed={failed} ===",
        run_id=run_id, **stats,
    )
    return stats
