import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    project: str
    bucket: str
    database: str = "(default)"
    athlete_id: str = "0"
    timezone: str = "Europe/Oslo"
    history_start_date: str = "2000-01-01"
    physiology_lookback_days: int = 42
    intervals_requests_per_run: int = 80
    intervals_budget_pool: str = "personal"
    billing_provider: str = "vipps"
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_price_id: str | None = None
    vipps_client_id: str | None = None
    vipps_client_secret: str | None = None
    vipps_subscription_key: str | None = None
    vipps_merchant_serial_number: str | None = None
    vipps_api_url: str = "https://api.vipps.no"
    vipps_price_amount: int = 19900
    vipps_currency: str = "NOK"
    dashboard_url: str = "http://localhost:8000/dashboard"

    @classmethod
    def from_env(cls):
        return cls(
            project=os.environ["GCP_PROJECT_ID"],
            bucket=os.environ["GCS_BUCKET"],
            database=os.getenv("FIRESTORE_DATABASE", "(default)"),
            athlete_id=os.getenv("INTERVALS_ATHLETE_ID", "0"),
            timezone=os.getenv("ATHLETE_TIMEZONE", "Europe/Oslo"),
            history_start_date=os.getenv("HISTORY_START_DATE", "2000-01-01"),
            physiology_lookback_days=int(os.getenv("PHYSIOLOGY_LOOKBACK_DAYS", "42")),
            intervals_requests_per_run=int(os.getenv("INTERVALS_REQUESTS_PER_RUN", "80")),
            intervals_budget_pool=os.getenv("INTERVALS_BUDGET_POOL", "personal"),
            billing_provider=os.getenv("BILLING_PROVIDER", "vipps"),
            stripe_secret_key=os.getenv("STRIPE_SECRET_KEY"),
            stripe_webhook_secret=os.getenv("STRIPE_WEBHOOK_SECRET"),
            stripe_price_id=os.getenv("STRIPE_PRICE_ID"),
            vipps_client_id=os.getenv("VIPPS_CLIENT_ID"),
            vipps_client_secret=os.getenv("VIPPS_CLIENT_SECRET"),
            vipps_subscription_key=os.getenv("VIPPS_SUBSCRIPTION_KEY"),
            vipps_merchant_serial_number=os.getenv("VIPPS_MERCHANT_SERIAL_NUMBER"),
            vipps_api_url=os.getenv("VIPPS_API_URL", "https://api.vipps.no"),
            vipps_price_amount=int(os.getenv("VIPPS_PRICE_AMOUNT", "19900")),
            vipps_currency=os.getenv("VIPPS_CURRENCY", "NOK"),
            dashboard_url=os.getenv("DASHBOARD_URL", "http://localhost:8000/dashboard"),
        )
