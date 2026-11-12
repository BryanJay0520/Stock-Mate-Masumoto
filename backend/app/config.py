"""Application configuration.

All settings come from environment variables (prefix ``STOCKMATE_``) or a
local ``.env`` file, so no secret ever lives in the source code (decision D12).
Sensible defaults are provided for local development only.
"""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator

# backend/app/config.py -> parents[2] is the project root (StockMate/)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime settings. Override any value with STOCKMATE_<NAME> env vars."""

    model_config = SettingsConfigDict(
        env_prefix="STOCKMATE_",
        env_file=PROJECT_ROOT / "backend" / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "StockMate"
    app_version: str = "0.1.0"
    environment: str = "development"

    # SQLite file for local development; PostgreSQL URL in production.
    database_url: str = f"sqlite:///{(PROJECT_ROOT / 'backend' / 'stockmate.db').as_posix()}"

    # Business calendar settings (decisions D5 / D18).
    business_timezone: str = "Asia/Tokyo"
    business_day_start_time: str = "00:00"  # HH:MM local; when the business date rolls over

    # Placeholder for the session-signing secret added in Phase 1 Step 3.
    # The default is intentionally insecure and only usable in development.
    secret_key: str = "dev-only-insecure-secret-change-me"

    # Session & login security (Phase 1 Step 3).
    session_ttl_hours: int = 12
    secure_cookies: bool = False  # set True behind HTTPS in production
    login_rate_limit_attempts: int = 5
    login_rate_limit_window_seconds: int = 600

    # Inventory (Phase 1 Step 4). Adjustments whose absolute delta exceeds
    # this many units require the server-validated confirmed=true flag.
    large_adjustment_threshold: Decimal = Decimal("500")

    # Read-only physical-count freshness caps used by forecast views.
    count_high_freshness_hours: int = Field(default=24, gt=0)
    count_medium_freshness_hours: int = Field(default=72, gt=0)

    @model_validator(mode="after")
    def valid_count_freshness(self):
        if self.count_medium_freshness_hours < self.count_high_freshness_hours:
            raise ValueError("Medium count freshness must be at least the high freshness window.")
        return self

    # Directory with the static frontend served by FastAPI.
    static_dir: Path = PROJECT_ROOT / "frontend"


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance (one per process)."""
    return Settings()
