# Shopify → Comatic Bridge

A lightweight Python service that syncs Shopify orders into a local SQLite database and presents them in a clean web dashboard. It is designed to track order financials and fulfillment status, and will serve as the foundation for automated Comatic ERP invoice creation.

---

## Architecture

```
Shopify Admin API
  │
  ├─ REST   →  paginated order ID list  (bulk, incremental)
  └─ GraphQL →  full order detail per order
                 (shipping, line items, discounts, product tags/collections)
                           │
                     SQLite (sync.db)
                      ├─ orders       (one row per order)
                      └─ order_items  (one row per line item)
                           │
                     FastAPI Dashboard  http://localhost:8000
```

---

## Setup

### 1. Clone and create a virtual environment

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

### 2. Configure `.env`

Copy `.env.example` to `.env` and fill in your values:

```env
# Shopify Custom App credentials (from Shopify Admin → Apps → Develop apps)
SHOPIFY_SHOP=your-store.myshopify.com
SHOPIFY_CLIENT_ID=your_client_id
SHOPIFY_CLIENT_SECRET=your_client_secret

# How many hours back the incremental sync looks (default: 25h)
SYNC_LOOKBACK_HOURS=25

# Bank account names → account codes shown in "Mark as Paid" dropdown
COMATIC_PAYMENT_ACCOUNTS={"Wise": "1001", "Bank Transfer": "1002", "Stripe": "1003"}

# SQLite database file path
SQLITE_DB_PATH=sync.db
```

### 3. Run the dashboard

```bash
python start_dashboard.py
```

Open **http://localhost:8000** in your browser.

---

## Syncing Orders

### Quick Sync (incremental)
Fetches orders created in the last `SYNC_LOOKBACK_HOURS` hours.  
Click **↻ Quick Sync** in the sidebar, or run:

```bash
python sync_runner.py
```

### Full Sync (historical)
Fetches all orders from the **last 365 days**.  
Click **⟳ Full Sync (365 days)** in the sidebar.

> **Note:** Use Full Sync when setting up for the first time or after a long outage.

---

## Dashboard Features

| Feature | Description |
|---|---|
| **3 Tabs** | All orders / Unpaid / Unfulfilled. Items that are both appear in both tabs. |
| **Order detail popup** | Click any row to see full shipping address, line items, discounts, collections and tags. |
| **Mark as Paid** | Available on unpaid orders. Select the bank account that received the payment. |
| **Revenue stats** | Shown per currency — no misleading cross-currency totals. |

---

## Running Tests

```bash
.venv\Scripts\python.exe -m pytest tests/ -v
```

The test suite covers the core data-transformation logic (order parsing, discount math, GID stripping, JSON serialisation) with **24 unit tests** — no network or database required.

---

## Project Structure

```
Shopify_Comatic/
├── config.py                  # Pydantic settings (reads .env)
├── database.py                # SQLite schema + async query helpers
├── sync_runner.py             # Cron/CLI entrypoint for incremental sync
├── start_dashboard.py         # Starts the FastAPI server
├── test_connection.py         # Ad-hoc API connectivity test script
│
├── services/
│   ├── shopify_client.py      # Async Shopify REST + GraphQL client
│   └── sync_engine.py        # Full sync pipeline (REST→GraphQL→SQLite)
│
├── models/
│   └── shopify.py             # Pydantic models for REST API responses
│
├── web/
│   ├── app.py                 # FastAPI routes
│   ├── templates/
│   │   └── dashboard.html     # Jinja2 template (~80 lines, pure markup)
│   └── static/
│       ├── dashboard.css      # All styles
│       └── dashboard.js       # Table rendering + modal logic
│
└── tests/
    └── test_sync_engine.py    # Unit tests for parse functions
```

---

## Shopify App Requirements

Your Shopify Custom App needs the following **API scopes**:

- `read_orders`
- `read_products`

The app uses the **Client Credentials OAuth flow** — no browser redirect needed.
