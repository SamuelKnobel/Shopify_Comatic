"""
services/comatic_client.py — Async Comatic WebApi client.

Auth: HTTP Basic (COMATIC_USERNAME + COMATIC_PASSWORD).
All endpoints confirmed from Comatic API documentation.
"""
from __future__ import annotations

from typing import Optional
import httpx
from loguru import logger

from config import settings
from models.comatic import (
    ComaticAddress,
    ComaticAddressCreate,
    ComaticAddressListResponse,
    ComaticInvoiceCreate,
    ComaticPaymentOnAccountCreate,
)


class ComaticAPIError(Exception):
    """Raised when the Comatic API returns a non-2xx response."""


class ComaticClient:
    """
    Async Comatic WebApi client.
    Usage:
        async with ComaticClient() as comatic:
            address = await comatic.find_address_by_email("john@example.com")
    """

    def __init__(self) -> None:
        self._base = (
            f"{settings.comatic_base_url.rstrip('/')}"
            f"/api/{settings.comatic_api_version}"
        )
        self._auth = (settings.comatic_username, settings.comatic_password)
        self._client: Optional[httpx.AsyncClient] = None
        self._vat_codes_cache: Optional[list[dict]] = None

    async def __aenter__(self) -> "ComaticClient":
        self._client = httpx.AsyncClient(
            auth=self._auth,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=30,
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    # ─── Internal helpers ────────────────────────────────────────────────────

    async def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self._base}{path}"
        logger.debug("Comatic GET {url} params={params}", url=url, params=params)
        response = await self._client.get(url, params=params)
        if not response.is_success:
            body = response.text[:500]
            logger.error(
                "Comatic API error {status} on GET {url}: {body}",
                status=response.status_code, url=url, body=body,
            )
            raise ComaticAPIError(
                f"Comatic GET {url} returned {response.status_code}: {body}"
            )
        return response.json()

    async def _post(self, path: str, body: dict) -> dict:
        url = f"{self._base}{path}"
        logger.debug("Comatic POST {url} body_keys={keys}", url=url, keys=list(body.keys()))
        response = await self._client.post(url, json=body)
        if not response.is_success:
            body_text = response.text[:500]
            logger.error(
                "Comatic API error {status} on POST {url}: {body}",
                status=response.status_code, url=url, body=body_text,
            )
            raise ComaticAPIError(
                f"Comatic POST {url} returned {response.status_code}: {body_text}"
            )
        return response.json()

    # ─── VAT codes ───────────────────────────────────────────────────────────

    async def get_vat_codes(self) -> list[dict]:
        """
        Fetch VAT code list from GET /api/{v}/masterdatas/vatcodes.
        Result is cached in memory for the duration of the client session.
        """
        if self._vat_codes_cache is not None:
            return self._vat_codes_cache
        try:
            data = await self._get("/masterdatas/vatcodes")
            codes = data.get("Data", []) or []
            self._vat_codes_cache = codes
            logger.info("Loaded {n} VAT codes from Comatic", n=len(codes))
            return codes
        except ComaticAPIError as exc:
            logger.warning(
                "Could not load VAT codes from Comatic ({err}). Using default.",
                err=str(exc),
            )
            return []

    # ─── Addresses ───────────────────────────────────────────────────────────

    async def find_address_by_email(self, email: str) -> Optional[ComaticAddress]:
        """
        Search Comatic addresses by email using the filter query parameter.
        Returns the first matching active Debtor, or None.
        """
        logger.info("Comatic: searching address for email={email}", email=email)
        try:
            data = await self._get(
                "/addresses",
                params={"addressesRequestParameters.filter": email, "addressesRequestParameters.pageSize": 5},
            )
            parsed = ComaticAddressListResponse(**data)
            addresses = parsed.Data or []
            # Look for an exact email match among results
            for addr in addresses:
                if addr.Email and addr.Email.lower() == email.lower():
                    logger.info(
                        "Comatic: found existing address Id={id} for {email}",
                        id=addr.Id,
                        email=email,
                    )
                    return addr
            logger.info("Comatic: no existing address found for {email}", email=email)
            return None
        except ComaticAPIError:
            logger.exception("Comatic: exception while searching address for {email}", email=email)
            return None

    async def create_address(self, address: ComaticAddressCreate) -> ComaticAddress:
        """
        Create a new Debtor address in Comatic.
        POST /api/{v}/addresses
        """
        logger.info(
            "Comatic: creating address for {first} {last} <{email}>",
            first=address.Firstname,
            last=address.Lastname,
            email=address.Email,
        )
        body = address.model_dump(exclude_none=True)
        data = await self._post("/addresses", body)
        created = ComaticAddress(**data.get("Data", data))
        logger.info(
            "Comatic: address created Id={id}", id=created.Id
        )
        return created

    # ─── Customer Invoices ───────────────────────────────────────────────────

    async def create_invoice(self, invoice: ComaticInvoiceCreate) -> dict:
        """
        Create a new Customer Invoice in Comatic.
        POST /api/{v}/customerinvoices

        - ExternalId stores the Shopify Order ID (max 50 chars).
        - IsOpenPosition=True → pending/unpaid.
        - IsOpenPosition=False → already paid.
        Returns the full response dict including the new invoice's Id and DocumentNumber.
        """
        logger.info(
            "Comatic: creating invoice for AddressId={addr} ExternalId={ext} IsOpen={open}",
            addr=invoice.AddressId,
            ext=invoice.ExternalId,
            open=invoice.IsOpenPosition,
        )
        body = invoice.model_dump(exclude_none=True)
        data = await self._post("/customerinvoices", body)
        invoice_data = data.get("Data", data)
        inv_id = invoice_data.get("Id") if isinstance(invoice_data, dict) else None
        doc_num = invoice_data.get("DocumentNumber") if isinstance(invoice_data, dict) else None
        logger.info(
            "Comatic: invoice created Id={id} DocumentNumber={doc}",
            id=inv_id,
            doc=doc_num,
        )
        return invoice_data if isinstance(invoice_data, dict) else data

    # ─── Payment on Account ──────────────────────────────────────────────────

    async def create_payment_on_account(
        self,
        address_id: int,
        payment: ComaticPaymentOnAccountCreate,
    ) -> dict:
        """
        Post a payment to close/settle an open invoice.
        POST /api/{v}/addresses/{addressId}/payments/paymentonaccount

        Body shape (from Comatic API docs):
            {AddressId, BookingText, Reference, Currency, Amount,
             BookingAmount, BookingDate, DocumentNumber, VatCode, Account, CostUnit}
        """
        logger.info(
            "Comatic: posting payment on account for addressId={addr} "
            "DocumentNumber={doc} Amount={amt} {currency}",
            addr=address_id,
            doc=payment.DocumentNumber,
            amt=payment.Amount,
            currency=payment.Currency,
        )
        body = payment.model_dump(exclude_none=True)
        data = await self._post(
            f"/addresses/{address_id}/payments/paymentonaccount", body
        )
        logger.info(
            "Comatic: payment on account posted successfully for addressId={addr}",
            addr=address_id,
        )
        return data
