"""
models/shopify.py — Pydantic models mirroring the Shopify Admin REST API response shapes.
Only the fields needed for the sync bridge are captured.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ShopifyMoneyAmount(BaseModel):
    amount: str
    currency_code: str


class ShopifyProduct(BaseModel):
    id: int
    product_type: Optional[str] = None


class ShopifyAddress(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    address1: Optional[str] = None
    address2: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    zip: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None
    phone: Optional[str] = None


class ShopifyLineItem(BaseModel):
    id: int
    title: str
    quantity: int
    price: str                          # String in Shopify API
    sku: Optional[str] = None
    variant_id: Optional[int] = None
    product_id: Optional[int] = None
    taxable: bool = True
    tax_lines: list[dict] = Field(default_factory=list)

    @property
    def unit_price_float(self) -> float:
        return float(self.price)

    @property
    def total_price_float(self) -> float:
        return self.unit_price_float * self.quantity

    @property
    def effective_tax_rate(self) -> float:
        """Sum of all tax line rates on this line item."""
        return sum(float(t.get("rate", 0)) for t in self.tax_lines)


class ShopifyShippingLine(BaseModel):
    title: str
    price: str
    tax_lines: list[dict] = Field(default_factory=list)

    @property
    def price_float(self) -> float:
        return float(self.price)

    @property
    def effective_tax_rate(self) -> float:
        return sum(float(t.get("rate", 0)) for t in self.tax_lines)


class ShopifyCustomer(BaseModel):
    id: int
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    default_address: Optional[ShopifyAddress] = None


class ShopifyOrder(BaseModel):
    id: int
    name: str                           # e.g. "#1001"
    email: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    financial_status: str               # "paid" | "pending" | "refunded" | etc.
    fulfillment_status: Optional[str] = None
    payment_gateway: Optional[str] = None  # "manual" | "stripe" | "shopify_payments" etc.
    currency: str
    total_price: str
    subtotal_price: Optional[str] = None
    total_tax: Optional[str] = None
    customer: Optional[ShopifyCustomer] = None
    billing_address: Optional[ShopifyAddress] = None
    shipping_address: Optional[ShopifyAddress] = None
    line_items: list[ShopifyLineItem] = Field(default_factory=list)
    shipping_lines: list[ShopifyShippingLine] = Field(default_factory=list)
    total_discounts: str = "0.00"
    tags: str = ""
    note: Optional[str] = None

    @property
    def total_price_float(self) -> float:
        return float(self.total_price)

    @property
    def customer_email(self) -> Optional[str]:
        if self.email:
            return self.email
        if self.customer and self.customer.email:
            return self.customer.email
        return None

    @property
    def customer_first_name(self) -> str:
        if self.customer:
            return self.customer.first_name or ""
        if self.billing_address:
            return self.billing_address.first_name or ""
        return ""

    @property
    def customer_last_name(self) -> str:
        if self.customer:
            return self.customer.last_name or ""
        if self.billing_address:
            return self.billing_address.last_name or ""
        return "Unknown"

    @property
    def country_code(self) -> str:
        if self.billing_address and self.billing_address.country_code:
            return self.billing_address.country_code
        return "CH"  # Default fallback


class ShopifyOrdersResponse(BaseModel):
    orders: list[ShopifyOrder]
