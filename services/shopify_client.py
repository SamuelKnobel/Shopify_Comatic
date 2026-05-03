"""
services/shopify_client.py — Async Shopify Admin REST API client.

Handles:
- Authentication via X-Shopify-Access-Token header
- Rate limit back-off (X-Shopify-Shop-Api-Call-Limit)
- Fetching orders created in the last N hours with any financial status
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from loguru import logger

from config import settings
from models.shopify import ShopifyOrder, ShopifyOrdersResponse


class ShopifyAPIError(Exception):
    """Raised when the Shopify API returns a non-2xx response."""


class ShopifyClient:
    """
    Thin async wrapper around the Shopify Admin REST API.
    Usage:
        async with ShopifyClient() as client:
            orders = await client.get_new_orders()
    """

    BASE_URL = "https://{store}/admin/api/2026-04"
    PAGE_LIMIT = 250  # Shopify max per page

    def __init__(self) -> None:
        self._base = self.BASE_URL.format(store=settings.shopify_shop)
        self._headers = {
            "Content-Type": "application/json",
        }
        # If a token is already provided in .env, use it
        if settings.shopify_token:
            self._headers["X-Shopify-Access-Token"] = settings.shopify_token

        self._client: Optional[httpx.AsyncClient] = None
        self._product_cache: dict[int, dict] = {}

    async def __aenter__(self) -> "ShopifyClient":
        await self._ensure_token()
        self._client = httpx.AsyncClient(headers=self._headers, timeout=30)
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    async def _ensure_token(self) -> None:
        """
        Ensures we have a valid X-Shopify-Access-Token.
        If missing, fetches one using Client ID and Secret.
        """
        if "X-Shopify-Access-Token" in self._headers:
            return

        logger.info("Shopify: Fetching fresh access token using client credentials...")
        token_url = f"https://{settings.shopify_shop}/admin/oauth/access_token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": settings.shopify_client_id,
            "client_secret": settings.shopify_client_secret,
        }

        async with httpx.AsyncClient() as temp_client:
            response = await temp_client.post(token_url, data=payload)
            if not response.is_success:
                logger.error("Failed to fetch Shopify token: {body}", body=response.text)
                raise ShopifyAPIError(f"Token retrieval failed: {response.status_code}")
            
            token = response.json().get("access_token")
            if not token:
                raise ShopifyAPIError("Shopify response did not contain access_token")
            
            self._headers["X-Shopify-Access-Token"] = token
            logger.info("Shopify: Access token retrieved successfully.")

    # ─── Internal helpers ────────────────────────────────────────────────────

    async def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self._base}{path}"
        logger.debug("Shopify GET {url} params={params}", url=url, params=params)
        response = await self._client.get(url, params=params)
        self._handle_rate_limit(response)
        if not response.is_success:
            logger.error(
                "Shopify API error {status} for {url}: {body}",
                status=response.status_code,
                url=url,
                body=response.text[:500],
            )
            raise ShopifyAPIError(
                f"Shopify returned {response.status_code} for {url}: {response.text[:200]}"
            )
        return response.json()

    async def _post(self, path: str, body: dict) -> dict:
        url = f"{self._base}{path}"
        logger.debug("Shopify POST {url}", url=url)
        response = await self._client.post(url, json=body)
        self._handle_rate_limit(response)
        if not response.is_success:
            logger.error(
                "Shopify API error {status} for POST {url}: {body}",
                status=response.status_code,
                url=url,
                body=response.text[:500],
            )
            raise ShopifyAPIError(
                f"Shopify returned {response.status_code} for POST {url}: {response.text[:200]}"
            )
        return response.json()

    async def graphql(self, query: str, variables: dict | None = None) -> dict:
        """
        Execute a GraphQL query against the Shopify Admin API.
        """
        url = f"{self._base}/graphql.json"
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        logger.debug("Shopify GraphQL POST to {url}", url=url)
        response = await self._client.post(url, json=payload)
        self._handle_rate_limit(response)

        if not response.is_success:
            logger.error("Shopify GraphQL error {status}: {body}", status=response.status_code, body=response.text)
            raise ShopifyAPIError(f"GraphQL HTTP Error: {response.status_code}")

        data = response.json()
        if "errors" in data:
            logger.error("Shopify GraphQL Errors: {errors}", errors=data["errors"])
            raise ShopifyAPIError(f"GraphQL Data Error: {data['errors']}")

        return data.get("data", {})

    @staticmethod
    def _handle_rate_limit(response: httpx.Response) -> None:
        """
        Shopify rate limit: "X-Shopify-Shop-Api-Call-Limit: used/max"
        Back off when close to the limit.
        """
        header = response.headers.get("X-Shopify-Shop-Api-Call-Limit", "")
        if header:
            try:
                used, total = map(int, header.split("/"))
                if used >= total - 5:
                    logger.warning(
                        "Shopify rate limit nearly hit ({used}/{total}). Sleeping 2s.",
                        used=used,
                        total=total,
                    )
                    # asyncio.sleep must be awaited; use a small blocking sleep here
                    # because we're inside a sync section. In practice calls are slow
                    # enough that we never hit this without warning.
                    import time; time.sleep(2)
            except ValueError:
                pass

    # ─── Public API ──────────────────────────────────────────────────────────

    async def get_new_orders(
        self,
        since_hours: int | None = None,
    ) -> list[ShopifyOrder]:
        """
        Fetch all orders created in the last `since_hours` hours (default: settings value).
        Returns all financial statuses so we can handle paid & pending separately.
        Iterates through all pages automatically.
        """
        lookback = since_hours or settings.sync_lookback_hours
        since_dt = datetime.now(timezone.utc) - timedelta(hours=lookback)
        since_iso = since_dt.isoformat()

        logger.info(
            "Fetching Shopify orders created since {since} (last {h} hours)",
            since=since_iso,
            h=lookback,
        )

        all_orders: list[ShopifyOrder] = []
        params: dict = {
            "created_at_min": since_iso,
            "limit": self.PAGE_LIMIT,
            "status": "any",
        }

        while True:
            data = await self._get("/orders.json", params=params)
            orders_batch = ShopifyOrdersResponse(**data).orders
            all_orders.extend(orders_batch)
            logger.debug("Fetched {n} orders in this page", n=len(orders_batch))

            if len(orders_batch) < self.PAGE_LIMIT:
                break  # No more pages

            # Shopify cursor-based pagination uses Link header
            # For simplicity we use since_id pagination here
            last_id = orders_batch[-1].id
            params["since_id"] = last_id

        logger.info(
            "Total Shopify orders fetched: {total}", total=len(all_orders)
        )
        return all_orders

    async def get_shop_info(self) -> dict:
        """Fetch general store settings to verify connection and permissions."""
        logger.info("Shopify: Verifying connection by fetching shop info...")
        data = await self._get("/shop.json")
        return data.get("shop", {})

    async def mark_order_paid(self, shopify_order_id: int, amount: float, currency: str) -> dict:
        """
        Post a payment transaction to Shopify to mark the order as paid.
        Called as part of the "Mark as Paid" dual-action.
        """
        logger.info(
            "Posting Shopify payment transaction for order {id} amount={amount} {currency}",
            id=shopify_order_id,
            amount=amount,
            currency=currency,
        )
        body = {
            "transaction": {
                "kind": "capture",
                "status": "success",
                "amount": str(amount),
                "currency": currency,
            }
        }
        response = await self._post(f"/orders/{shopify_order_id}/transactions.json", body)
        logger.info(
            "Shopify order {id} marked as paid via transaction.",
            id=shopify_order_id,
        )
        return response

    async def get_product(self, product_id: int) -> dict:
        """
        Fetch product details (like product_type) from Shopify.
        Uses a simple in-memory cache to minimize redundant API calls.
        """
        if product_id in self._product_cache:
            return self._product_cache[product_id]

        logger.debug("Shopify: fetching product details for id={id}", id=product_id)
        data = await self._get(f"/products/{product_id}.json")
        product = data.get("product", {})
        self._product_cache[product_id] = product
        return product

    async def get_products(self, limit: int = 5) -> list[dict]:
        """
        Fetch a list of products from Shopify.
        Useful for testing product API permissions.
        """
        logger.info("Shopify: Fetching {limit} products...", limit=limit)
        data = await self._get("/products.json", params={"limit": limit})
        return data.get("products", [])

    async def download_order_document(self, shopify_order_id: int) -> str:
        """
        Mock document downloader. Saves Order JSON to order_docs_dir.
        Requirement: "for now... please just log itin the console"
        """
        import os
        import json
        
        logger.info("[DOCS] Downloading document for Shopify order {id}...", id=shopify_order_id)
        
        # We'll download the JSON data as the "document" for now
        data = await self._get(f"/orders/{shopify_order_id}.json")
        
        os.makedirs(settings.order_docs_dir, exist_ok=True)
        file_path = os.path.join(settings.order_docs_dir, f"order_{shopify_order_id}.json")
        
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        logger.debug("[DOCS] Document saved to {path}", path=file_path)
        return file_path
