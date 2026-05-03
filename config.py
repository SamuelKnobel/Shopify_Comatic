"""
config.py — Reads all settings from the .env file via pydantic-settings.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Shopify ─────────────────────────────────────────────────────────────────
    shopify_shop: str           # e.g. "your-store.myshopify.com"
    shopify_client_id: str
    shopify_client_secret: str
    shopify_token: str = ""     # Optional; fetched dynamically if empty

    def __init__(self, **values):
        super().__init__(**values)
        self.shopify_shop = self.shopify_shop.strip()
        self.shopify_client_id = self.shopify_client_id.strip()
        self.shopify_client_secret = self.shopify_client_secret.strip()
        self.shopify_token = self.shopify_token.strip() if self.shopify_token else ""

    # ── Comatic ─────────────────────────────────────────────────────────────────
    comatic_base_url: str       # e.g. "https://comatic.example.com"
    comatic_api_version: str = "v3"
    comatic_username: str
    comatic_password: str
    comatic_default_vat_code: str = "NN"          # fallback; override once confirmed
    comatic_default_sales_condition: str = "30"   # 2-char terms code
    comatic_default_purchase_condition: str = "30"
    comatic_default_stock_location: int = 1
    comatic_default_vat_type: int = 1
    comatic_default_charge_factor: int = 1
    comatic_default_accounting_rate: float = 1.0

    # ── Accountant Settings ─────────────────────────────────────────────────────
    comatic_shipping_article_id: int = 9000
    comatic_vat_mapping: str = "{}"  # Parsed as json
    comatic_payment_accounts: str = "{}" # JSON Mapping: "Name": "AccountCode"
    order_docs_dir: str = "documents"
    eur_chf_rate: float = 0.95

    # ── SQLite ───────────────────────────────────────────────────────────────────
    sqlite_db_path: str = "sync.db"

    # ── Logging ──────────────────────────────────────────────────────────────────
    log_file: str = "shopify_comatic.log"

    # ── Sync ─────────────────────────────────────────────────────────────────────
    sync_lookback_hours: int = 24

    # Dashboard Protection
    dashboard_username: str = ""
    dashboard_password: str = ""  # If both are set, protection is enabled


settings = Settings()
