"""Data models for sources, leads, and extracted entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SourceType(str, Enum):
    """Type of content source."""

    RSS = "rss"
    HTML = "html"
    SITEMAP = "sitemap"
    CALENDAR = "calendar"
    DIRECTORY = "directory"
    SOCIAL = "social"
    JOB_BOARD = "job_board"


class SourceCategory(str, Enum):
    """Category of the source organization."""

    TRADE_MEDIA = "trade_media"
    CHAMBER = "chamber"
    STATE_ASSOCIATION = "state_association"
    DEALER_GROUP = "dealer_group"
    DEALER_SITE = "dealer_site"
    LOCAL_NEWS = "local_news"
    JOB_SITE = "job_site"
    ECONOMIC_DEV = "economic_dev"
    OTHER = "other"


class LeadScore(str, Enum):
    """Three-tier lead confidence scoring."""

    HOT = "hot"      # 2+ mentions or 1 strong mention from trade/chamber/dealer
    WARM = "warm"    # Staffing, hiring, teaser, construction, coming-soon language
    COLD = "cold"    # Weak mention, still worth parking in the queue


@dataclass
class Source:
    """A registered content source (one URL endpoint)."""

    id: int | None = None
    url: str = ""
    source_type: SourceType = SourceType.HTML
    category: SourceCategory = SourceCategory.OTHER
    name: str = ""
    geography: str = ""  # State, metro area, or "national"
    priority: int = 5  # 1 = highest, 10 = lowest
    enabled: bool = True
    parent_source_id: int | None = None  # If discovered from another source
    last_fetched_at: datetime | None = None
    last_hash: str = ""  # Content hash for change detection
    created_at: datetime | None = None
    fetch_error_count: int = 0
    notes: str = ""


@dataclass
class FetchResult:
    """Result of fetching a single source endpoint."""

    source_id: int
    url: str
    status_code: int = 0
    content: str = ""
    content_hash: str = ""
    links: list[str] = field(default_factory=list)
    fetched_at: datetime | None = None
    error: str | None = None


@dataclass
class Lead:
    """An extracted lead signal from crawled content."""

    id: int | None = None
    source_id: int = 0
    source_url: str = ""
    title: str = ""
    snippet: str = ""  # The text fragment that triggered the match
    dealer_name: str = ""
    dealer_group: str = ""
    city: str = ""
    state: str = ""
    people: list[str] = field(default_factory=list)
    keywords_matched: list[str] = field(default_factory=list)
    outbound_links: list[str] = field(default_factory=list)
    score: LeadScore = LeadScore.COLD
    mention_count: int = 1
    discovered_at: datetime | None = None
    raw_text: str = ""


@dataclass
class DiscoveredSource:
    """A new source URL discovered during crawling, pending registration."""

    url: str = ""
    source_type: SourceType = SourceType.HTML
    category: SourceCategory = SourceCategory.OTHER
    discovered_from_source_id: int = 0
    geography: str = ""
    reason: str = ""  # Why we think this is worth crawling
