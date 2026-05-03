"""
tests/test_sync_engine.py

Unit tests for the pure data-transformation functions in services/sync_engine.py.
These tests require NO network access and NO database — they work entirely on
in-memory dicts that mimic real Shopify GraphQL responses.

Run with:
    .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
import json
import pytest

from services.sync_engine import _parse_order, _parse_line_items


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — minimal GraphQL response shapes
# ─────────────────────────────────────────────────────────────────────────────

def _make_money(amount: str, currency: str = "EUR") -> dict:
    return {"shopMoney": {"amount": amount, "currencyCode": currency}}


def _make_line_item(
    title: str,
    qty: int,
    original: str,
    discounted: str,
    discount_amounts: list[str] | None = None,
    sku: str = "SKU-1",
    product_id: str = "gid://shopify/Product/111",
    product_type: str = "LAVYL",
    collections: list[str] | None = None,
    tags: list[str] | None = None,
    line_item_id: str = "gid://shopify/LineItem/999",
) -> dict:
    return {
        "node": {
            "id": line_item_id,
            "title": title,
            "quantity": qty,
            "sku": sku,
            "product": {
                "id": product_id,
                "productType": product_type,
                "tags": tags or [],
                "collections": {
                    "edges": [{"node": {"title": c}} for c in (collections or [])]
                },
            },
            "originalUnitPriceSet":   _make_money(original),
            "discountedUnitPriceSet": _make_money(discounted),
            "discountAllocations": [
                {"allocatedAmountSet": _make_money(a)}
                for a in (discount_amounts or [])
            ],
        }
    }


SAMPLE_ORDER = {
    "name": "#1042",
    "createdAt": "2026-01-15T09:00:00Z",
    "displayFinancialStatus": "PAID",
    "displayFulfillmentStatus": "FULFILLED",
    "currencyCode": "EUR",
    "paymentGatewayNames": ["shopify_payments"],
    "totalPriceSet": _make_money("59.80"),
    "totalShippingPriceSet": _make_money("8.90"),
    "customer": {
        "id": "gid://shopify/Customer/42",
        "email": "test@example.com",
    },
    "shippingAddress": {
        "name": "Anna Beispiel",
        "address1": "Hauptstrasse 1",
        "address2": None,
        "city": "Berlin",
        "zip": "10115",
        "provinceCode": None,
        "countryCodeV2": "DE",
    },
    "fulfillments": [{"createdAt": "2026-01-17T12:00:00Z"}],
    "lineItems": {
        "edges": [
            _make_line_item(
                title="Lippenpflege 15ml",
                qty=2,
                original="14.90",
                discounted="11.80",
                discount_amounts=["6.20"],
                sku="LIPP-15",
                collections=["LAVYL", "BESTSELLER"],
                tags=["organic"],
            ),
            _make_line_item(
                title="Mega-Set 10-Stk",
                qty=1,
                original="57.00",
                discounted="57.00",
                discount_amounts=[],
                sku="SET-10",
                collections=["SPAR-SET"],
                tags=["SET"],
                line_item_id="gid://shopify/LineItem/888",
                product_id="gid://shopify/Product/222",
            ),
        ]
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# _parse_order tests
# ─────────────────────────────────────────────────────────────────────────────

class TestParseOrder:
    def test_basic_fields(self):
        result = _parse_order(SAMPLE_ORDER, "6253000001")
        assert result["shopify_order_id"]    == "6253000001"
        assert result["shopify_order_name"]  == "#1042"
        assert result["currency"]            == "EUR"
        assert result["financial_status"]    == "PAID"
        assert result["fulfillment_status"]  == "FULFILLED"

    def test_prices(self):
        result = _parse_order(SAMPLE_ORDER, "1")
        assert result["total_price"]   == pytest.approx(59.80)
        assert result["shipping_cost"] == pytest.approx(8.90)

    def test_customer_fields(self):
        result = _parse_order(SAMPLE_ORDER, "1")
        assert result["customer_email"]       == "test@example.com"
        assert result["shopify_customer_id"]  == "gid://shopify/Customer/42"

    def test_shipping_address(self):
        result = _parse_order(SAMPLE_ORDER, "1")
        assert result["shipping_name"]         == "Anna Beispiel"
        assert result["shipping_city"]         == "Berlin"
        assert result["shipping_country_code"] == "DE"
        assert result["shipping_zip"]          == "10115"

    def test_payment_gateway_first_entry(self):
        result = _parse_order(SAMPLE_ORDER, "1")
        assert result["payment_gateway"] == "shopify_payments"

    def test_shipped_date_from_fulfillment(self):
        result = _parse_order(SAMPLE_ORDER, "1")
        assert result["shipped_date"] == "2026-01-17T12:00:00Z"

    def test_no_fulfillment_gives_none(self):
        order = {**SAMPLE_ORDER, "fulfillments": []}
        result = _parse_order(order, "1")
        assert result["shipped_date"] is None

    def test_no_shipping_address(self):
        order = {**SAMPLE_ORDER, "shippingAddress": None}
        result = _parse_order(order, "1")
        assert result["shipping_city"] is None
        assert result["shipping_country_code"] is None

    def test_no_customer(self):
        order = {**SAMPLE_ORDER, "customer": None}
        result = _parse_order(order, "1")
        assert result["customer_email"] is None

    def test_no_payment_gateway(self):
        order = {**SAMPLE_ORDER, "paymentGatewayNames": []}
        result = _parse_order(order, "1")
        assert result["payment_gateway"] is None


# ─────────────────────────────────────────────────────────────────────────────
# _parse_line_items tests
# ─────────────────────────────────────────────────────────────────────────────

class TestParseLineItems:
    def test_correct_number_of_items(self):
        items = _parse_line_items(SAMPLE_ORDER, "1", "shopify_payments")
        assert len(items) == 2

    def test_item_title_and_sku(self):
        items = _parse_line_items(SAMPLE_ORDER, "1", "shopify_payments")
        assert items[0]["product_name"] == "Lippenpflege 15ml"
        assert items[0]["sku"]          == "LIPP-15"

    def test_total_paid_discounted(self):
        """total_paid = discounted_unit_price * quantity"""
        items = _parse_line_items(SAMPLE_ORDER, "1", "shopify_payments")
        item = items[0]  # 11.80 * 2 = 23.60
        assert item["discounted_unit_price"] == pytest.approx(11.80)
        assert item["quantity"] == 2
        assert item["total_paid"] == pytest.approx(23.60)

    def test_total_paid_no_discount(self):
        """total_paid == original when no discount applied"""
        items = _parse_line_items(SAMPLE_ORDER, "1", "shopify_payments")
        item = items[1]  # 57.00 * 1 = 57.00
        assert item["total_paid"] == pytest.approx(57.00)

    def test_discount_sum(self):
        """total_discount sums all allocatedAmountSet values"""
        items = _parse_line_items(SAMPLE_ORDER, "1", "shopify_payments")
        assert items[0]["total_discount"] == pytest.approx(6.20)
        assert items[1]["total_discount"] == pytest.approx(0.00)

    def test_discount_formula_consistency(self):
        """
        Verify: (original * qty) - total_discount ≈ total_paid
        This is the formula Shopify guarantees.
        """
        items = _parse_line_items(SAMPLE_ORDER, "1", "shopify_payments")
        for item in items:
            expected = round(
                (item["original_unit_price"] * item["quantity"]) - item["total_discount"], 2
            )
            assert item["total_paid"] == pytest.approx(expected, abs=0.01), (
                f"Discount formula mismatch for {item['product_name']}: "
                f"expected {expected}, got {item['total_paid']}"
            )

    def test_multiple_discounts_summed(self):
        """Two discount allocations on one item should be summed."""
        order = {
            **SAMPLE_ORDER,
            "lineItems": {
                "edges": [
                    _make_line_item(
                        title="Multi-Discount Item",
                        qty=1,
                        original="100.00",
                        discounted="80.00",
                        discount_amounts=["10.00", "10.00"],  # two separate discounts
                    )
                ]
            },
        }
        items = _parse_line_items(order, "1", None)
        assert items[0]["total_discount"] == pytest.approx(20.00)
        assert items[0]["total_paid"]     == pytest.approx(80.00)

    def test_gid_stripped_from_ids(self):
        """Product and line item IDs should be plain numeric strings, not gid://."""
        items = _parse_line_items(SAMPLE_ORDER, "1", "shopify_payments")
        assert items[0]["shopify_line_item_id"] == "999"
        assert items[0]["shopify_product_id"]   == "111"

    def test_collections_stored_as_json(self):
        items = _parse_line_items(SAMPLE_ORDER, "1", None)
        cols = json.loads(items[0]["collections"])
        assert "LAVYL" in cols
        assert "BESTSELLER" in cols

    def test_tags_stored_as_json(self):
        items = _parse_line_items(SAMPLE_ORDER, "1", None)
        tags = json.loads(items[0]["tags"])
        assert "organic" in tags

    def test_no_collections_gives_empty_list(self):
        order = {
            **SAMPLE_ORDER,
            "lineItems": {
                "edges": [
                    _make_line_item(
                        title="No Collections Item", qty=1,
                        original="10.00", discounted="10.00",
                        collections=[],
                    )
                ]
            },
        }
        items = _parse_line_items(order, "1", None)
        assert json.loads(items[0]["collections"]) == []

    def test_payment_gateway_inherited(self):
        """Each item inherits the order-level payment gateway."""
        items = _parse_line_items(SAMPLE_ORDER, "1", "wise")
        for item in items:
            assert item["payment_gateway"] == "wise"

    def test_order_name_propagated(self):
        items = _parse_line_items(SAMPLE_ORDER, "6253000001", "shopify_payments")
        for item in items:
            assert item["shopify_order_name"] == "#1042"
            assert item["shopify_order_id"]   == "6253000001"

    def test_empty_line_items(self):
        order = {**SAMPLE_ORDER, "lineItems": {"edges": []}}
        items = _parse_line_items(order, "1", None)
        assert items == []
