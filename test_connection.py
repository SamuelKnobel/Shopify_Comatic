import asyncio
from loguru import logger
from services.shopify_client import ShopifyClient, ShopifyAPIError
from logging_setup import setup_logging

async def test_shopify():
    setup_logging()
    logger.info("Starting Shopify connection test...")
    
    try:
        async with ShopifyClient() as shopify:
            # 1. Test basic connection (Shop info)
            shop = await shopify.get_shop_info()
            print("\n✅ CONNECTION SUCCESSFUL!")
            print(f"Store Name: {shop.get('name')}")
            print(f"Email:      {shop.get('email')}")
            print(f"Currency:   {shop.get('currency')}")
            print(f"Plan:       {shop.get('plan_name')}")
            
            # 2. Test Order fetching (limit 1 for testing)
            print("\n📡 Testing Order Access...")
            h = 365*24
            orders = await shopify.get_new_orders(since_hours=h) # check last x days
            if orders:
                print(f"Successfully fetched {len(orders)} recent orders.")
                first = orders[0]
                print(f"Latest Order Example: {first.name} ({first.total_price} {first.currency})")
                print("\n--- Order JSON ---")
                # Assuming ShopifyOrder is a Pydantic model (v2)
                try:
                    print(first.model_dump_json(indent=2))
                except AttributeError:
                    # Fallback for Pydantic v1 or dict
                    import json
                    print(json.dumps(dict(first), indent=2, default=str))
                print("------------------\n")
            else:
                print(f"No orders found in the last {h/24} days, but connection works.")

            # 3. Test Product fetching
            print("\n📦 Testing Product Access...")
            products = await shopify.get_products(limit=3)
            if products:
                print(f"Successfully fetched {len(products)} products.")
                first_product = products[0]
                print(f"Product Example: {first_product.get('title')} (ID: {first_product.get('id')})")
                print("\n--- Product JSON ---")
                import json
                print(json.dumps(first_product, indent=2, default=str))
                print("--------------------\n")
            else:
                print("No products found, but connection works.")

            # 4. Test GraphQL Query
            if products:
                print("\n🧬 Testing GraphQL Query...")
                first_product_id = products[0].get("id")
                # GraphQL uses Global IDs (GID)
                gid = f"gid://shopify/Product/{first_product_id}"
                
                query = """
                query($id: ID!) {
                  product(id: $id) {
                    title
                    collections(first: 5) {
                      edges {
                        node {
                          title
                          handle
                        }
                      }
                    }
                    tags
                  }
                }
                """
                variables = {"id": gid}
                
                try:
                    gql_data = await shopify.graphql(query, variables)
                    print("✅ GraphQL SUCCESS!")
                    print(f"Product: {gql_data['product']['title']}")
                    print(f"Tags: {', '.join(gql_data['product']['tags'])}")
                    
                    # Extract collection titles
                    collections = [edge['node']['title'] for edge in gql_data['product']['collections']['edges']]
                    print(f"Collections: {', '.join(collections) if collections else 'None'}")
                    
                    print("\n--- GraphQL Full JSON ---")
                    print(json.dumps(gql_data, indent=2))
                    print("-------------------------\n")
                except Exception as e:
                    print(f"❌ GraphQL Query Failed: {e}")

            # 5. Test GraphQL Order Detail (shipping + line items + discounts)
            if orders:
                print("\n🧾 Testing GraphQL Order Detail...")
                first_order_id = orders[0].id
                order_gid = f"gid://shopify/Order/{first_order_id}"

                order_query = """
                query($id: ID!) {
                  order(id: $id) {
                    name
                    totalShippingPriceSet {
                      shopMoney { amount currencyCode }
                    }
                    shippingLines(first: 5) {
                      edges {
                        node {
                          title
                          originalPriceSet {
                            shopMoney { amount currencyCode }
                          }
                          discountedPriceSet {
                            shopMoney { amount currencyCode }
                          }
                          taxLines {
                            title
                            rate
                            priceSet {
                              shopMoney { amount currencyCode }
                            }
                          }
                        }
                      }
                    }
                    lineItems(first: 50) {
                      edges {
                        node {
                          title
                          quantity
                          sku
                          product {
                            id
                            productType
                            tags
                            collections(first: 3) {
                              edges { node { title handle } }
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
                            discountApplication {
                              ... on DiscountCodeApplication {
                                code
                                value {
                                  ... on PricingPercentageValue { percentage }
                                  ... on MoneyV2 { amount currencyCode }
                                }
                              }
                              ... on AutomaticDiscountApplication { title }
                              ... on ManualDiscountApplication { title }
                            }
                          }
                        }
                      }
                    }
                  }
                }
                """

                try:
                    order_gql = await shopify.graphql(order_query, {"id": order_gid})
                    order_data = order_gql.get("order", {})
                    
                    print(f"✅ Order: {order_data.get('name')}")
                    
                    # Shipping summary
                    shipping_total = order_data.get("totalShippingPriceSet", {}).get("shopMoney", {})
                    print(f"📦 Total Shipping: {shipping_total.get('amount')} {shipping_total.get('currencyCode')}")
                    
                    # Line items summary
                    print("\n📋 Line Items:")
                    for edge in order_data.get("lineItems", {}).get("edges", []):
                        node = edge["node"]
                        original_unit = node.get("originalUnitPriceSet", {}).get("shopMoney", {})
                        discounted_unit = node.get("discountedUnitPriceSet", {}).get("shopMoney", {})
                        qty = node.get("quantity", 1)
                        currency = discounted_unit.get("currencyCode", "")
                        # Calculate total paid: discountedUnitPrice * quantity (discountedTotalPriceSet not available in 2026-04)
                        total_paid = round(float(discounted_unit.get("amount", 0)) * qty, 2)
                        discount_count = len(node.get("discountAllocations", []))
                        product = node.get("product") or {}
                        collections = [e["node"]["title"] for e in (product.get("collections") or {}).get("edges", [])]
                        
                        print(f"  - {node['title']} x{qty}")
                        print(f"    List: {original_unit.get('amount')} / Paid/unit: {discounted_unit.get('amount')} / Total paid: {total_paid} {currency}")
                        if discount_count:
                            print(f"    Discounts applied: {discount_count}")
                        if collections:
                            print(f"    Collections: {', '.join(collections)}")
                        if product.get("tags"):
                            print(f"    Tags: {', '.join(product['tags'])}")
                    
                    print("\n--- Full Order GraphQL JSON ---")
                    print(json.dumps(order_gql, indent=2))
                    print("------------------------------\n")
                except Exception as e:
                    print(f"❌ Order GraphQL Query Failed: {e}")

    except ShopifyAPIError as e:
        print(f"\n❌ SHOPIFY API ERROR: {e}")
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test_shopify())
