"""
models/comatic.py — Pydantic models mirroring the Comatic WebApi request/response shapes.
Field names match the Comatic API exactly (PascalCase) for direct serialisation.
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Address
# ─────────────────────────────────────────────────────────────────────────────

class ComaticAddressCreate(BaseModel):
    """Body for POST /api/{v}/addresses"""
    Salutation: str = Field(max_length=16)
    Firstname: str = Field(max_length=50)
    Lastname: str = Field(max_length=50)
    Address01: Optional[str] = Field(None, max_length=50)
    Address02: Optional[str] = Field(None, max_length=50)
    Postalcode: Optional[str] = Field(None, max_length=10)
    City: str = Field(max_length=50)
    Region: Optional[str] = Field(None, max_length=50)
    CountryCode: str = Field(max_length=3)             # ISO 3166 alpha-2, e.g. "CH"
    LanguageCode: str = Field(max_length=5)            # RFC 4646, e.g. "de-ch"
    Email: Optional[str] = Field(None, max_length=255)
    Telephone01: Optional[str] = Field(None, max_length=30)
    CurrencyCode: Optional[str] = Field(None, max_length=3)   # ISO 4217, e.g. "CHF"
    AddressStatus: str = "Active"                      # Enum: Active | Locked | Inactive
    AddressType: str = "Debtor"                        # Enum: Debtor | Creditor | Payroll
    DefaultSalesConditionCode: str = Field(max_length=2)
    DefaultPurchaseConditionCode: str = Field(max_length=2)
    ExternalId: Optional[str] = Field(None, max_length=50)    # Shopify customer ID


class ComaticAddress(BaseModel):
    """Response shape from GET /api/{v}/addresses"""
    Id: Optional[int] = None
    Salutation: Optional[str] = None
    Firstname: Optional[str] = None
    Lastname: Optional[str] = None
    Email: Optional[str] = None
    City: Optional[str] = None
    CountryCode: Optional[str] = None
    AddressType: Optional[str] = None
    ExternalId: Optional[str] = None


class ComaticAddressListResponse(BaseModel):
    Data: Optional[list[ComaticAddress]] = None
    HasErrors: Optional[bool] = None
    Errors: Optional[list[str]] = None


# ─────────────────────────────────────────────────────────────────────────────
# Invoice detail row
# ─────────────────────────────────────────────────────────────────────────────

class ComaticInvoiceDetailRow(BaseModel):
    """One line item in a CustomerInvoice (ICustomerInvoiceDetailRow)."""
    ArticleId: Optional[int] = None        # Comatic article ID (None = free-text line)
    Description: Optional[str] = None
    Specification: Optional[str] = None
    Quantity: Optional[float] = None
    SingleAmount: Optional[float] = None   # Unit price excl. VAT
    TotalAmount: Optional[float] = None    # Total excl. VAT
    Discount: Optional[float] = None
    VatCode: Optional[str] = Field(None, max_length=3)
    QuantityUnit: Optional[str] = None
    ExternalId: Optional[str] = None       # Shopify line item ID


# ─────────────────────────────────────────────────────────────────────────────
# Invoice header
# ─────────────────────────────────────────────────────────────────────────────

class ComaticInvoiceCreate(BaseModel):
    """Body for POST /api/{v}/customerinvoices"""
    DocumentDate: str                               # ISO 8601, e.g. "2024-01-15T00:00:00Z"
    Status: str = "Registered"                      # Enum: Wait | Registered | Printed
    AddressId: int
    AddressForServiceId: int                        # Same as AddressId for B2C
    InvoiceAddressId: int                           # Same as AddressId for B2C
    CommissionRecipientAddressId: int               # Same as AddressId (no commission agent)
    Terms: str = Field(max_length=2)               # Payment terms code
    ChargeFactor: float = 1.0
    VatType: int = 1
    CurrencyCode: str = Field(max_length=3)
    AccountingRate: float = 1.0
    StockLocation: int = 1
    StatusOfStock: int = 0
    IsOpenPosition: bool = True                     # False = paid on creation
    ExternalId: Optional[str] = Field(None, max_length=50)   # Shopify Order ID
    Reference: Optional[str] = Field(None, max_length=50)    # Shopify order name e.g. "#1001"
    BookingText: Optional[str] = Field(None, max_length=50)
    DetailRows: list[ComaticInvoiceDetailRow] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Payment on account
# ─────────────────────────────────────────────────────────────────────────────

class ComaticPaymentOnAccountCreate(BaseModel):
    """Body for POST /api/{v}/addresses/{addressId}/payments/paymentonaccount"""
    AddressId: Optional[int] = None
    BookingText: Optional[str] = None
    Reference: Optional[str] = None
    Currency: Optional[str] = None
    Amount: Optional[float] = None
    BookingAmount: Optional[float] = None
    BookingDate: Optional[str] = None               # ISO 8601
    DocumentNumber: Optional[str] = None            # Comatic invoice DocumentNumber
    VatCode: Optional[str] = None
    Account: Optional[str] = None
    CostUnit: Optional[str] = None
