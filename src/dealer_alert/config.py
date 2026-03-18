"""Configuration management via environment variables and .env files."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _bool(val: str) -> bool:
    return val.lower() in ("true", "1", "yes")


@dataclass
class Config:
    """Application configuration loaded from environment."""

    # Database
    database_path: Path = Path("data/dealer_alert.db")

    # Crawl settings
    max_concurrent_fetches: int = 10
    fetch_timeout_seconds: int = 30
    crawl_delay_seconds: float = 2.0
    user_agent: str = "DealerAlertBot/1.0 (lead-research)"
    max_redirects: int = 5

    # Output
    output_dir: Path = Path("data/digests")
    output_format: str = "both"  # csv, json, or both

    # Logging
    log_level: str = "INFO"
    log_file: Path | None = None

    # Expansion engine
    auto_expand: bool = True
    max_expansion_depth: int = 2
    max_new_sources_per_crawl: int = 100

    # Scoring
    hot_lead_min_mentions: int = 2
    warm_lead_keywords: list[str] = field(
        default_factory=lambda: ["hiring", "construction", "coming-soon", "teaser"]
    )

    # Email collector (Gmail OAuth2)
    email_address: str = ""
    gmail_credentials_file: Path = Path("client_secret.json")
    gmail_token_file: Path = Path("data/gmail_token.json")
    email_max_per_run: int = 100
    email_mark_read: bool = True

    # Social monitor
    social_max_concurrent: int = 5
    social_delay_seconds: float = 2.0

    @classmethod
    def load(cls, env_file: Path | None = None) -> Config:
        """Load config from .env file and environment variables."""
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()

        log_file_raw = os.getenv("LOG_FILE", "")
        warm_raw = os.getenv("WARM_LEAD_KEYWORDS", "")

        return cls(
            database_path=Path(os.getenv("DATABASE_PATH", "data/dealer_alert.db")),
            max_concurrent_fetches=int(os.getenv("MAX_CONCURRENT_FETCHES", "10")),
            fetch_timeout_seconds=int(os.getenv("FETCH_TIMEOUT_SECONDS", "30")),
            crawl_delay_seconds=float(os.getenv("CRAWL_DELAY_SECONDS", "2")),
            user_agent=os.getenv("USER_AGENT", "DealerAlertBot/1.0 (lead-research)"),
            max_redirects=int(os.getenv("MAX_REDIRECTS", "5")),
            output_dir=Path(os.getenv("OUTPUT_DIR", "data/digests")),
            output_format=os.getenv("OUTPUT_FORMAT", "both"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_file=Path(log_file_raw) if log_file_raw else None,
            auto_expand=_bool(os.getenv("AUTO_EXPAND", "true")),
            max_expansion_depth=int(os.getenv("MAX_EXPANSION_DEPTH", "2")),
            max_new_sources_per_crawl=int(os.getenv("MAX_NEW_SOURCES_PER_CRAWL", "100")),
            hot_lead_min_mentions=int(os.getenv("HOT_LEAD_MIN_MENTIONS", "2")),
            warm_lead_keywords=(
                warm_raw.split(",")
                if warm_raw
                else ["hiring", "construction", "coming-soon", "teaser"]
            ),
            # Email collector (Gmail OAuth2)
            email_address=os.getenv("GMAIL_ADDRESS", ""),
            gmail_credentials_file=Path(
                os.getenv(
                    "GMAIL_CREDENTIALS_FILE",
                    "client_secret.json",
                )
            ),
            gmail_token_file=Path(
                os.getenv(
                    "GMAIL_TOKEN_FILE",
                    "data/gmail_token.json",
                )
            ),
            email_max_per_run=int(os.getenv("EMAIL_MAX_PER_RUN", "100")),
            email_mark_read=_bool(os.getenv("EMAIL_MARK_READ", "true")),
            # Social monitor
            social_max_concurrent=int(os.getenv("SOCIAL_MAX_CONCURRENT", "5")),
            social_delay_seconds=float(os.getenv("SOCIAL_DELAY_SECONDS", "2")),
        )
