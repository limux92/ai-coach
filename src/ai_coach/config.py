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
        )
